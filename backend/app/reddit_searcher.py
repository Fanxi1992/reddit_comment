import json
import logging
import os
import queue
import random
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests
from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from app.schemas import PlannedQuery, RedditSearchRequest, RedditSearchResultItem, RedditSearchSummary, SearchFilterCriteria


load_dotenv()

logger = logging.getLogger(__name__)

SUPPORTED_SEARCH_TIME_RANGES = frozenset({"all", "month", "week"})
SUPPORTED_SEARCH_SORTS = frozenset({"relevance"})
REDDIT_SEARCH_NAVIGATION_TIMEOUT_MS = 20_000
DEFAULT_SEARCH_ENV_CONCURRENCY = 3
DEFAULT_SEARCH_QUERIES_PER_ENV = 2
DEFAULT_SEARCH_MAX_SCAN_PER_QUERY = 150
ADSPOWER_API_MIN_INTERVAL_SECONDS = 1.1
ADSPOWER_BROWSER_START_RETRIES = 4

_adspower_api_lock = threading.Lock()
_adspower_last_api_call_at = 0.0


def build_reddit_search_url(
    target_url: str,
    query: str,
    *,
    search_sort: str,
    search_time: str,
) -> str:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("reddit_search_query_required")
    if search_sort not in SUPPORTED_SEARCH_SORTS:
        raise ValueError(f"unsupported_search_sort:{search_sort}")
    if search_time not in SUPPORTED_SEARCH_TIME_RANGES:
        raise ValueError(f"unsupported_search_time:{search_time}")

    parsed_target = urlsplit(target_url.strip())
    if (
        parsed_target.scheme.lower() not in {"http", "https"}
        or not parsed_target.netloc
        or not _is_reddit_search_hostname(parsed_target.hostname)
        or parsed_target.username is not None
        or parsed_target.password is not None
    ):
        raise ValueError(f"unsupported_reddit_target_url:{target_url}")

    query_string = urlencode(
        [
            ("q", normalized_query),
            ("type", "posts"),
            ("sort", search_sort),
            ("t", search_time),
        ]
    )
    return urlunsplit((parsed_target.scheme.lower(), parsed_target.netloc, "/search/", query_string, ""))


def is_expected_reddit_search_url(
    url: str,
    *,
    query: str,
    search_sort: str,
    search_time: str,
) -> bool:
    try:
        parsed = urlsplit(url.strip())
        params = parse_qs(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return False

    normalized_path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")
    return (
        parsed.scheme.lower() in {"http", "https"}
        and _is_reddit_search_hostname(parsed.hostname)
        and normalized_path == "/search"
        and params.get("q") == [query.strip()]
        and params.get("type") == ["posts"]
        and params.get("sort") == [search_sort]
        and params.get("t") == [search_time]
    )


def _is_reddit_search_hostname(hostname: str | None) -> bool:
    normalized = (hostname or "").lower().rstrip(".")
    return normalized == "reddit.com" or normalized.endswith(".reddit.com")


@dataclass
class RawSearchResult:
    query: str
    result_index: int
    post_url: str
    post_id: str
    title: str
    subreddit: str
    age_text: str
    votes: int | None
    comments: int | None
    raw_text: str


@dataclass
class QuerySearchResult:
    status: str
    reason: str
    search_results_url: str
    results: list[RawSearchResult]
    raw_url_count: int
    scanned_result_count: int = 0
    qualified_result_count: int = 0
    rejected_result_count: int = 0
    filter_reject_counts: dict[str, int] | None = None
    target_reached: bool = False


@dataclass
class SearchCollectionOutcome:
    results: list[RawSearchResult]
    scanned_result_count: int
    rejected_result_count: int
    filter_reject_counts: dict[str, int]
    target_reached: bool


@dataclass
class AdsPowerSettings:
    api_url: str
    api_key: str
    user_id: str
    target_url: str = "https://www.reddit.com"
    env_id: str = "env-1"


class SearchResultSelector:
    def __init__(self, *, target_count: int, search_filter: SearchFilterCriteria | None, max_scan_count: int) -> None:
        self.target_count = max(1, target_count)
        self.search_filter = search_filter
        self.max_scan_count = max(1, max_scan_count)
        self.seen_urls: set[str] = set()
        self.results: list[RawSearchResult] = []
        self.scanned_result_count = 0
        self.rejected_result_count = 0
        self.filter_reject_counts: dict[str, int] = {}

    @property
    def target_reached(self) -> bool:
        return len(self.results) >= self.target_count

    @property
    def scan_limit_reached(self) -> bool:
        return self.scanned_result_count >= self.max_scan_count

    def should_stop(self) -> bool:
        return self.target_reached or self.scan_limit_reached

    def add(self, item: RawSearchResult) -> bool:
        if self.should_stop():
            return False

        normalized_url = normalize_post_url(item.post_url)
        if not normalized_url or normalized_url in self.seen_urls:
            return False

        self.seen_urls.add(normalized_url)
        self.scanned_result_count += 1
        item.post_url = normalized_url
        item.result_index = self.scanned_result_count

        reject_reason = evaluate_search_filter_reject_reason(item, self.search_filter)
        if reject_reason:
            self.rejected_result_count += 1
            self.filter_reject_counts[reject_reason] = self.filter_reject_counts.get(reject_reason, 0) + 1
            return True

        self.results.append(item)
        return True

    def outcome(self) -> SearchCollectionOutcome:
        return SearchCollectionOutcome(
            results=self.results[: self.target_count],
            scanned_result_count=self.scanned_result_count,
            rejected_result_count=self.rejected_result_count,
            filter_reject_counts=dict(self.filter_reject_counts),
            target_reached=self.target_reached,
        )


class RedditSearchRunner:
    def __init__(self, settings: AdsPowerSettings) -> None:
        self.settings = settings
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "RedditSearchRunner":
        ws_url = self._start_browser()
        if not ws_url:
            raise RuntimeError("adspower_browser_start_failed")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(ws_url)
        self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._close_existing_reddit_pages()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        if not self._stop_browser():
            logger.warning(
                "AdsPower search environment stop failed: env_id=%s user_id=%s",
                self.settings.env_id,
                self.settings.user_id,
            )

    def _close_existing_reddit_pages(self) -> None:
        if self._context is None:
            return
        for page in list(self._context.pages):
            try:
                page_url = (page.url or "").lower()
            except Exception:
                continue
            if "reddit.com" not in page_url:
                continue
            try:
                page.close()
            except Exception as exc:
                logger.warning(
                    "Failed to close existing reddit search tab: env_id=%s user_id=%s url=%s error=%s",
                    self.settings.env_id,
                    self.settings.user_id,
                    page_url,
                    exc,
                )

    def collect_query(
        self,
        query: str,
        *,
        search_sort: str,
        search_time: str,
        target_count: int,
        search_filter: SearchFilterCriteria | None,
        max_scan_count: int,
    ) -> QuerySearchResult:
        if self._context is None:
            raise RuntimeError("browser_context_not_initialized")

        page = self._context.new_page()
        try:
            search_results_url = self._navigate_to_search_results_page(
                page,
                query,
                search_sort=search_sort,
                search_time=search_time,
            )
            outcome = self._collect_search_results_until_target(
                page,
                query,
                target_count,
                search_filter=search_filter,
                max_scan_count=max_scan_count,
            )
            if not outcome.results and self._is_no_results_page(page):
                return _make_query_search_result("no_results", "no_results", search_results_url, outcome)
            status = "success" if outcome.results else "failed"
            reason = "" if outcome.results else "no_qualified_search_results_collected"
            if outcome.results and not outcome.target_reached:
                reason = "target_not_reached_after_scan_limit" if outcome.scanned_result_count >= max_scan_count else "target_not_reached"
            return _make_query_search_result(status, reason, search_results_url, outcome)
        except Exception as exc:
            return QuerySearchResult("failed", str(exc), "", [], 0)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _start_browser(self) -> str | None:
        url = f"{self.settings.api_url}/api/v1/browser/start"
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        params = {"user_id": self.settings.user_id, "headless": 0}
        last_error = ""
        for attempt in range(ADSPOWER_BROWSER_START_RETRIES):
            try:
                response = _rate_limited_adspower_get(url, params=params, headers=headers, timeout=30)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                last_error = str(exc)
            else:
                if payload.get("code") == 0:
                    return payload.get("data", {}).get("ws", {}).get("puppeteer")
                last_error = str(payload.get("msg") or "failed")

            if attempt >= ADSPOWER_BROWSER_START_RETRIES - 1:
                break
            time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(f"adspower_browser_start_failed:{last_error or 'unknown'}")

    def _stop_browser(self) -> bool:
        url = f"{self.settings.api_url}/api/v1/browser/stop"
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        params = {"user_id": self.settings.user_id}
        try:
            response = _rate_limited_adspower_get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return False
        return payload.get("code") == 0

    def _navigate_to_search_results_page(
        self,
        page: Page,
        query: str,
        *,
        search_sort: str,
        search_time: str,
    ) -> str:
        search_url = build_reddit_search_url(
            self.settings.target_url,
            query,
            search_sort=search_sort,
            search_time=search_time,
        )
        page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=REDDIT_SEARCH_NAVIGATION_TIMEOUT_MS,
        )
        self._settle_page(page)

        final_url = page.url
        if not is_expected_reddit_search_url(
            final_url,
            query=query,
            search_sort=search_sort,
            search_time=search_time,
        ):
            raise RuntimeError(f"unexpected_reddit_search_url:{final_url}")
        return final_url

    def _settle_page(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except PlaywrightTimeoutError:
            pass
        time.sleep(random.uniform(0.8, 1.4))

    def _collect_search_results_until_target(
        self,
        page: Page,
        query: str,
        target_count: int,
        *,
        search_filter: SearchFilterCriteria | None,
        max_scan_count: int,
    ) -> SearchCollectionOutcome:
        selector = SearchResultSelector(
            target_count=target_count,
            search_filter=search_filter,
            max_scan_count=max_scan_count,
        )
        no_growth_rounds = 0
        max_scroll_rounds = 30

        for scroll_round in range(max_scroll_rounds + 1):
            if self._is_no_results_page(page):
                return selector.outcome()

            round_items = self._collect_search_results(page, query, max_results=max(target_count * 3, 80, max_scan_count))
            new_count = 0
            for item in round_items:
                if selector.add(item):
                    new_count += 1
                if selector.should_stop():
                    break

            if selector.should_stop():
                outcome = selector.outcome()
                _log_suspicious_metadata_duplicates(query, outcome.results)
                return outcome
            if scroll_round >= max_scroll_rounds:
                outcome = selector.outcome()
                _log_suspicious_metadata_duplicates(query, outcome.results)
                return outcome
            if new_count == 0:
                no_growth_rounds += 1
                if selector.scanned_result_count and no_growth_rounds >= 8:
                    outcome = selector.outcome()
                    _log_suspicious_metadata_duplicates(query, outcome.results)
                    return outcome
            else:
                no_growth_rounds = 0
            visible_url_count = self._visible_search_result_url_count(page)
            self._perform_search_results_scroll(page)
            self._wait_for_search_results_to_settle(page, visible_url_count)
            self._settle_after_search_scroll(page)
        outcome = selector.outcome()
        _log_suspicious_metadata_duplicates(query, outcome.results)
        return outcome

    def _collect_search_results(self, page: Page, query: str, max_results: int) -> list[RawSearchResult]:
        raw_items = page.evaluate(
            """
            (maxResults) => {
                const text = (el) => el ? (el.innerText || el.textContent || "").trim() : "";
                const titleSelector = "a[data-testid='post-title-text'], a[data-testid='post-title']";
                const normalizeUrl = (href) => {
                    if (!href) return "";
                    return href.startsWith("http") ? href : `https://www.reddit.com${href}`;
                };
                const parseCount = (value) => {
                    const raw = (value || "").toString().trim().toLowerCase().replace(/,/g, "");
                    if (!raw) return null;
                    const match = raw.match(/(-?\\d+(?:\\.\\d+)?)\\s*([km])?/);
                    if (!match) return null;
                    let number = Number(match[1]);
                    const suffix = match[2];
                    if (suffix === "k") number *= 1000;
                    if (suffix === "m") number *= 1000000;
                    return Number.isFinite(number) ? Math.trunc(number) : null;
                };
                const countTitleAnchors = (root) => {
                    if (!root || !root.querySelectorAll) return 0;
                    const descendantCount = root.querySelectorAll(titleSelector).length;
                    return (root.matches && root.matches(titleSelector) ? 1 : 0) + descendantCount;
                };
                const findSubredditText = (root) => {
                    const subredditEl =
                        root.querySelector("a[aria-haspopup='dialog'][href^='/r/']:not([href*='/comments/']) .truncate") ||
                        root.querySelector("a[href^='/r/']:not([href*='/comments/']) .truncate") ||
                        root.querySelector("a[aria-haspopup='dialog'][href^='/r/']:not([href*='/comments/'])") ||
                        root.querySelector("a[href^='/r/']:not([href*='/comments/'])");
                    return text(subredditEl).split(/\\s+/)[0];
                };
                const isInvalidSearchBlock = (value) => {
                    const lowered = (value || "").toLowerCase();
                    return lowered.includes("people also search for")
                        || lowered.includes("view answers")
                        || /^answers\\b/i.test(lowered)
                        || /^posts\\b/i.test(lowered);
                };
                const findResultCard = (anchor) => {
                    let node = anchor;
                    for (let depth = 0; depth < 12 && node && node !== document.body; depth += 1) {
                        const titleCount = countTitleAnchors(node);
                        if (titleCount > 1) {
                            break;
                        }

                        const rawText = text(node);
                        const subredditText = findSubredditText(node);
                        const titleText = text(anchor) || anchor.getAttribute("aria-label") || "";
                        if (
                            titleCount === 1
                            && titleText
                            && /^r\\//i.test(subredditText)
                            && !isInvalidSearchBlock(rawText)
                        ) {
                            return {
                                root: node,
                                rawText,
                                subredditText,
                                titleText,
                                titleCount,
                            };
                        }
                        node = node.parentElement;
                    }
                    return null;
                };
                const anchors = Array.from(document.querySelectorAll(titleSelector));
                const items = [];
                const seen = new Set();

                for (const anchor of anchors) {
                    if (items.length >= maxResults) break;
                    const href = normalizeUrl(anchor.getAttribute("href") || "");
                    if (!href || seen.has(href) || !/\\/comments\\//i.test(href)) continue;

                    const card = findResultCard(anchor);
                    if (!card) continue;
                    const timeEl = card.root.querySelector("time");
                    const counterRow = card.root.querySelector("[data-testid='search-counter-row']");
                    const numberNodes = counterRow ? Array.from(counterRow.querySelectorAll("faceplate-number[number]")) : [];
                    const candidate = {
                        post_url: href,
                        title: card.titleText,
                        subreddit: card.subredditText,
                        age_text: timeEl ? text(timeEl) : "",
                        votes: numberNodes[0] ? parseCount(numberNodes[0].getAttribute("number") || text(numberNodes[0])) : null,
                        comments: numberNodes[1] ? parseCount(numberNodes[1].getAttribute("number") || text(numberNodes[1])) : null,
                        raw_text: `${card.rawText}\\n__debug__ titleAnchorText=${text(anchor)} containerTitleCount=${card.titleCount} containerTag=${card.root.tagName || ""}`,
                    };
                    seen.add(candidate.post_url);
                    items.push(candidate);
                }
                return items;
            }
            """,
            max_results,
        )

        items: list[RawSearchResult] = []
        for index, item in enumerate(raw_items or [], start=1):
            post_url = str(item.get("post_url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not post_url or not title:
                continue
            items.append(
                RawSearchResult(
                    query=query,
                    result_index=index,
                    post_url=post_url,
                    post_id=extract_post_id(post_url),
                    title=title,
                    subreddit=str(item.get("subreddit") or "").strip(),
                    age_text=str(item.get("age_text") or "").strip(),
                    votes=item.get("votes"),
                    comments=item.get("comments"),
                    raw_text=str(item.get("raw_text") or "").strip(),
                )
            )
        return items

    def _visible_search_result_url_count(self, page: Page) -> int:
        try:
            return int(
                page.evaluate(
                    """
                    () => {
                        const normalizeUrl = (href) => {
                            if (!href) return "";
                            return href.startsWith("http") ? href : `https://www.reddit.com${href}`;
                        };
                        const urls = new Set();
                        for (const anchor of document.querySelectorAll("a[data-testid='post-title-text'], a[data-testid='post-title']")) {
                            const rect = anchor.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0) continue;
                            const href = normalizeUrl(anchor.getAttribute("href") || "");
                            if (/\\/comments\\//i.test(href)) urls.add(href);
                        }
                        return urls.size;
                    }
                    """
                )
            )
        except Exception:
            return 0

    def _wait_for_search_results_to_settle(self, page: Page, previous_visible_url_count: int) -> None:
        deadline = time.time() + 4.0
        stable_rounds = 0
        last_count = -1
        while time.time() < deadline:
            current_count = self._visible_search_result_url_count(page)
            if current_count > previous_visible_url_count:
                return
            if current_count == last_count and current_count > 0:
                stable_rounds += 1
                if stable_rounds >= 3:
                    return
            else:
                stable_rounds = 0
                last_count = current_count
            time.sleep(0.25)

    def _perform_search_results_scroll(self, page: Page) -> None:
        viewport_height = int(
            page.evaluate("() => Math.max(window.innerHeight || 0, document.documentElement.clientHeight || 0, 700)")
        )
        target_distance = max(520, int(viewport_height * random.uniform(1.35, 1.75)))
        chunks = max(2, min(6, int(target_distance / 360)))
        travelled = 0
        for index in range(chunks):
            remaining = target_distance - travelled
            if remaining <= 0:
                break
            delta = remaining if index == chunks - 1 else min(remaining, random.randint(220, 420))
            page.mouse.wheel(0, delta)
            travelled += delta
            time.sleep(random.uniform(0.04, 0.1))
        time.sleep(random.uniform(0.35, 0.75))

    def _settle_after_search_scroll(self, page: Page) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=800)
        except PlaywrightTimeoutError:
            pass
        time.sleep(random.uniform(0.15, 0.35))

    def _is_no_results_page(self, page: Page) -> bool:
        try:
            signal = page.evaluate(
                """
                () => {
                    const text = (document.body.innerText || "").toLowerCase();
                    const hasVisibleResult = Array.from(document.querySelectorAll("a[data-testid='post-title'], a[data-testid='post-title-text']"))
                        .some((el) => {
                            const rect = el.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        });
                    const hasEmptyText =
                        text.includes("we couldn’t find any results")
                        || text.includes("we couldn't find any results")
                        || text.includes("double-check your spelling or try different keywords");
                    return { hasVisibleResult, hasEmptyText };
                }
                """
            )
        except Exception:
            return False
        return not bool(signal.get("hasVisibleResult")) and bool(signal.get("hasEmptyText"))


def load_adspower_settings() -> AdsPowerSettings:
    return load_adspower_search_profiles()[0]


def load_adspower_search_profiles() -> list[AdsPowerSettings]:
    api_url = os.getenv("ADSPOWER_API_URL", "").strip()
    api_key = os.getenv("ADSPOWER_API_KEY", "").strip()
    raw_user_ids = os.getenv("ADSPOWER_USER_IDS", "").strip()
    fallback_user_id = os.getenv("ADSPOWER_USER_ID", "").strip()
    target_url = os.getenv("REDDIT_TARGET_URL", "https://www.reddit.com").strip() or "https://www.reddit.com"
    user_ids = [item.strip() for item in raw_user_ids.split(",") if item.strip()] or ([fallback_user_id] if fallback_user_id else [])
    missing = [
        name
        for name, value in {
            "ADSPOWER_API_URL": api_url,
            "ADSPOWER_API_KEY": api_key,
            "ADSPOWER_USER_IDS 或 ADSPOWER_USER_ID": ",".join(user_ids),
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"缺少 AdsPower 配置: {', '.join(missing)}")
    return [
        AdsPowerSettings(
            api_url=api_url.rstrip("/"),
            api_key=api_key,
            user_id=user_id,
            target_url=target_url,
            env_id=f"search-env-{index}",
        )
        for index, user_id in enumerate(user_ids, start=1)
    ]


def run_reddit_search_batch(payload: RedditSearchRequest, stop_event: threading.Event | None = None):
    stop_event = stop_event or threading.Event()
    profiles = load_adspower_search_profiles()
    deduper = SearchResultDeduper()
    query_results: list[dict[str, Any]] = []
    query_assignments = list(enumerate(payload.queries, start=1))

    requested_concurrency = _load_search_env_concurrency()
    queries_per_env = _load_search_queries_per_env()
    needed_environment_count = (len(query_assignments) + queries_per_env - 1) // queries_per_env
    selected_profile_count = min(needed_environment_count, requested_concurrency, len(profiles), len(query_assignments))
    profiles = random.sample(profiles, selected_profile_count)
    chunks = _chunk_evenly(query_assignments, len(profiles))
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    threads: list[threading.Thread] = []
    max_scan_count = _load_search_max_scan_per_query()

    for environment_index, (settings, chunk) in enumerate(zip(profiles, chunks, strict=False), start=1):
        thread = threading.Thread(
            target=_run_search_environment_worker,
            args=(
                settings,
                environment_index,
                chunk,
                payload.perQueryLimit,
                payload.searchSort,
                payload.searchFilter,
                max_scan_count,
                event_queue,
                stop_event,
            ),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    completed_workers = 0
    pending_results: dict[int, dict[str, Any]] = {}
    next_query_index = 1
    completed_normally = False

    try:
        while completed_workers < len(threads):
            event = event_queue.get()
            if event is None:
                completed_workers += 1
                continue
            if event.get("type") == "_query_result_internal":
                pending_results[int(event["queryIndex"])] = event
                while next_query_index in pending_results:
                    query_event = pending_results.pop(next_query_index)
                    query_payload = _build_query_result_payload(
                        deduper=deduper,
                        query_index=next_query_index,
                        query=query_event["query"],
                        target_count=int(query_event["targetUrlCount"]),
                        result=query_event["result"],
                    )
                    query_results.append(query_payload)
                    yield query_payload
                    next_query_index += 1
                continue
            yield event

        if stop_event.is_set():
            return

        summary = build_summary(payload, query_results, deduper.results)
        yield {
            "type": "summary",
            "summary": summary.model_dump(),
            "results": [item.model_dump() for item in deduper.sorted_results()],
        }
        completed_normally = True
        yield {
            "type": "done",
            "summary": summary.model_dump(),
            "results": [item.model_dump() for item in deduper.sorted_results()],
        }
    finally:
        if not completed_normally:
            stop_event.set()
        for thread in threads:
            thread.join()


def _run_search_environment_worker(
    settings: AdsPowerSettings,
    environment_index: int,
    assignments: list[tuple[int, PlannedQuery]],
    fallback_target_count: int,
    search_sort: str,
    search_filter: SearchFilterCriteria | None,
    max_scan_count: int,
    event_queue: queue.Queue[dict[str, Any] | None],
    stop_event: threading.Event,
) -> None:
    processed_count = 0
    try:
        with RedditSearchRunner(settings) as runner:
            for query_index, query in assignments:
                if stop_event.is_set():
                    break
                target_count = _query_target_count(query, fallback_target_count)
                event_queue.put(
                    {
                        "type": "query_started",
                        "queryIndex": query_index,
                        "query": query.query,
                        "timeRange": query.suggestedTimeRange,
                        "targetUrlCount": target_count,
                        "environmentId": settings.env_id,
                        "environmentIndex": environment_index,
                    }
                )
                result = runner.collect_query(
                    query.query,
                    search_sort=search_sort,
                    search_time=query.suggestedTimeRange,
                    target_count=target_count,
                    search_filter=search_filter,
                    max_scan_count=max_scan_count,
                )
                event_queue.put(
                    {
                        "type": "_query_result_internal",
                        "queryIndex": query_index,
                        "query": query,
                        "targetUrlCount": target_count,
                        "result": result,
                    }
                )
                processed_count += 1
    except Exception as exc:
        if not stop_event.is_set():
            for query_index, query in assignments[processed_count:]:
                target_count = _query_target_count(query, fallback_target_count)
                event_queue.put(
                    {
                        "type": "query_started",
                        "queryIndex": query_index,
                        "query": query.query,
                        "timeRange": query.suggestedTimeRange,
                        "targetUrlCount": target_count,
                        "environmentId": settings.env_id,
                        "environmentIndex": environment_index,
                    }
                )
                event_queue.put(
                    {
                        "type": "_query_result_internal",
                        "queryIndex": query_index,
                        "query": query,
                        "targetUrlCount": target_count,
                        "result": QuerySearchResult("failed", f"环境启动或执行失败: {exc}", "", [], 0),
                    }
                )
    finally:
        event_queue.put(None)


def _make_query_search_result(
    status: str,
    reason: str,
    search_results_url: str,
    outcome: SearchCollectionOutcome,
) -> QuerySearchResult:
    return QuerySearchResult(
        status=status,
        reason=reason,
        search_results_url=search_results_url,
        results=outcome.results,
        raw_url_count=outcome.scanned_result_count,
        scanned_result_count=outcome.scanned_result_count,
        qualified_result_count=len(outcome.results),
        rejected_result_count=outcome.rejected_result_count,
        filter_reject_counts=outcome.filter_reject_counts,
        target_reached=outcome.target_reached,
    )


def _build_query_result_payload(
    *,
    deduper: "SearchResultDeduper",
    query_index: int,
    query: PlannedQuery,
    target_count: int,
    result: QuerySearchResult,
) -> dict[str, Any]:
    raw_items = [
        build_result_item(query, raw_item, duplicate_of_query=deduper.find_duplicate(raw_item.post_url))
        for raw_item in result.results
    ]
    for item in raw_items:
        deduper.add(item)
    return {
        "type": "query_result",
        "queryIndex": query_index,
        "query": query.query,
        "targetUrlCount": target_count,
        "status": result.status,
        "reason": result.reason,
        "searchResultsUrl": result.search_results_url,
        "rawResultCount": result.raw_url_count,
        "uniqueResultCount": sum(1 for item in raw_items if not item.duplicateOfQuery),
        "scannedResultCount": result.scanned_result_count,
        "qualifiedResultCount": result.qualified_result_count,
        "rejectedResultCount": result.rejected_result_count,
        "filterRejectCounts": result.filter_reject_counts or {},
        "targetReached": result.target_reached,
        "results": [item.model_dump() for item in raw_items],
    }


def _query_target_count(query: PlannedQuery, fallback_target_count: int) -> int:
    return query.targetUrlCount or fallback_target_count


def _load_search_env_concurrency() -> int:
    raw_value = os.getenv("SEARCH_ENV_CONCURRENCY", "").strip()
    if not raw_value:
        return DEFAULT_SEARCH_ENV_CONCURRENCY
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_SEARCH_ENV_CONCURRENCY


def _load_search_queries_per_env() -> int:
    raw_value = os.getenv("SEARCH_QUERIES_PER_ENV", "").strip()
    if not raw_value:
        return DEFAULT_SEARCH_QUERIES_PER_ENV
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_SEARCH_QUERIES_PER_ENV


def _load_search_max_scan_per_query() -> int:
    raw_value = os.getenv("REDDIT_SEARCH_MAX_SCAN_PER_QUERY", "").strip()
    if not raw_value:
        return DEFAULT_SEARCH_MAX_SCAN_PER_QUERY
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_SEARCH_MAX_SCAN_PER_QUERY


def _chunk_evenly(items: list[tuple[int, PlannedQuery]], fanout: int) -> list[list[tuple[int, PlannedQuery]]]:
    if fanout <= 1:
        return [items]
    base = len(items) // fanout
    remainder = len(items) % fanout
    chunks: list[list[tuple[int, PlannedQuery]]] = []
    cursor = 0
    for index in range(fanout):
        size = base + (1 if index < remainder else 0)
        chunk = items[cursor : cursor + size]
        if chunk:
            chunks.append(chunk)
        cursor += size
    return chunks


def _rate_limited_adspower_get(url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: int) -> requests.Response:
    global _adspower_last_api_call_at
    with _adspower_api_lock:
        elapsed = time.monotonic() - _adspower_last_api_call_at
        if elapsed < ADSPOWER_API_MIN_INTERVAL_SECONDS:
            time.sleep(ADSPOWER_API_MIN_INTERVAL_SECONDS - elapsed)
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        _adspower_last_api_call_at = time.monotonic()
        return response


def evaluate_search_filter_reject_reason(
    item: RawSearchResult,
    search_filter: SearchFilterCriteria | None,
) -> str | None:
    if not search_filter:
        return None

    if search_filter.maxAgeDays is not None:
        age_hours = parse_reddit_age_hours(item.age_text)
        if age_hours is None:
            return "missing_age"
        if age_hours > search_filter.maxAgeDays * 24:
            return "too_old"

    if search_filter.minVotes is not None:
        if item.votes is None:
            return "missing_votes"
        if item.votes < search_filter.minVotes:
            return "low_votes"

    if search_filter.minComments is not None:
        if item.comments is None:
            return "missing_comments"
        if item.comments < search_filter.minComments:
            return "low_comments"

    return None


def parse_reddit_age_hours(age_text: str) -> float | None:
    value = _normalize_visible_text(age_text).lower()
    if not value:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(mo|mon|mons|month|months|y|yr|yrs|year|years|d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b",
        value,
    )
    if not match:
        return None

    amount = float(match.group(1))
    unit = match.group(2)
    if unit in {"m", "min", "mins", "minute", "minutes"}:
        return amount / 60
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return amount
    if unit in {"d", "day", "days"}:
        return amount * 24
    if unit in {"mo", "mon", "mons", "month", "months"}:
        return amount * 30 * 24
    if unit in {"y", "yr", "yrs", "year", "years"}:
        return amount * 365 * 24
    return None


def _is_better_search_item(candidate: RawSearchResult, existing: RawSearchResult) -> bool:
    return _search_item_quality_score(candidate) > _search_item_quality_score(existing)


def _search_item_quality_score(item: RawSearchResult) -> int:
    score = 0
    if item.title.strip():
        score += 3
    if item.subreddit.strip().lower().startswith("r/"):
        score += 3
    if item.age_text.strip():
        score += 1
    if item.votes is not None:
        score += 1
    if item.comments is not None:
        score += 1
    if item.raw_text.strip():
        score += min(2, len(item.raw_text.strip()) // 120)
    return score


def _log_suspicious_metadata_duplicates(query: str, items: list[RawSearchResult]) -> None:
    buckets: dict[tuple[str, str, str, int | None, int | None], list[str]] = {}
    for item in items:
        signature = (
            _normalize_visible_text(item.title).lower(),
            item.subreddit.lower(),
            _normalize_visible_text(item.age_text).lower(),
            item.votes,
            item.comments,
        )
        if not signature[0] or not signature[1]:
            continue
        buckets.setdefault(signature, []).append(item.post_url)

    for signature, urls in buckets.items():
        if len(set(urls)) < 4:
            continue
        logger.warning(
            "Suspicious repeated Reddit search metadata: query=%s title=%s subreddit=%s age=%s votes=%s comments=%s url_count=%s",
            query,
            signature[0],
            signature[1],
            signature[2],
            signature[3],
            signature[4],
            len(set(urls)),
        )


class SearchResultDeduper:
    def __init__(self) -> None:
        self.results: dict[str, RedditSearchResultItem] = {}

    def find_duplicate(self, url: str) -> str | None:
        normalized = normalize_post_url(url)
        if not normalized:
            return None
        existing = self.results.get(normalized)
        return existing.query if existing else None

    def add(self, item: RedditSearchResultItem) -> None:
        normalized = normalize_post_url(item.postUrl)
        if not normalized:
            return
        existing = self.results.get(normalized)
        if existing:
            if item.query not in existing.matchedQueries:
                existing.matchedQueries.append(item.query)
            return
        item.postUrl = normalized
        item.matchedQueries = [item.query]
        self.results[normalized] = item

    def sorted_results(self) -> list[RedditSearchResultItem]:
        return sorted(self.results.values(), key=lambda item: (item.priority, item.query.lower(), item.resultIndex))


def build_result_item(query: PlannedQuery, raw_item: RawSearchResult, duplicate_of_query: str | None) -> RedditSearchResultItem:
    normalized_url = normalize_post_url(raw_item.post_url) or raw_item.post_url
    return RedditSearchResultItem(
        query=query.query,
        queryIntent=query.intent,
        priority=query.priority,
        timeRange=query.suggestedTimeRange,
        resultIndex=raw_item.result_index,
        postUrl=normalized_url,
        postId=extract_post_id(normalized_url),
        title=raw_item.title,
        subreddit=raw_item.subreddit,
        ageText=raw_item.age_text,
        votes=raw_item.votes,
        comments=raw_item.comments,
        duplicateOfQuery=duplicate_of_query,
        matchedQueries=[query.query],
    )


def build_summary(
    payload: RedditSearchRequest,
    query_results: list[dict[str, Any]],
    unique_results: dict[str, RedditSearchResultItem],
) -> RedditSearchSummary:
    successful_queries = sum(1 for item in query_results if item.get("status") == "success")
    raw_url_count = sum(int(item.get("rawResultCount") or 0) for item in query_results)
    return RedditSearchSummary(
        totalQueries=len(payload.queries),
        successfulQueries=successful_queries,
        failedQueries=len(payload.queries) - successful_queries,
        rawUrlCount=raw_url_count,
        uniqueUrlCount=len(unique_results),
    )


def normalize_post_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if not parsed.scheme:
        parsed = urlsplit(f"https://www.reddit.com{value if value.startswith('/') else '/' + value}")
    host = parsed.netloc.lower()
    if host == "redd.it" or host.endswith(".redd.it"):
        path = re.sub(r"/+", "/", parsed.path).rstrip("/")
        return urlunsplit(("https", "redd.it", path, "", "")) if path else ""
    if host != "reddit.com" and not host.endswith(".reddit.com"):
        return ""
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    match = re.match(r"^(/r/[^/]+/comments/[^/]+(?:/[^/]+)?)", path, flags=re.IGNORECASE)
    if match:
        path = match.group(1).rstrip("/")
    return urlunsplit(("https", "www.reddit.com", path, "", ""))


def extract_post_id(url: str) -> str:
    match = re.search(r"/comments/([^/?#]+)/?", url or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""


def encode_ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _normalize_visible_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())
