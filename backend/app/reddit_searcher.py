import json
import math
import os
import queue
import random
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from app.schemas import PlannedQuery, RedditSearchRequest, RedditSearchResultItem, RedditSearchSummary


load_dotenv()

DEFAULT_SEARCH_URL_PREFIX = "https://www.reddit.com/search/?q="
SEARCH_TIME_LABELS = {
    "all": "All time",
    "month": "Past month",
    "week": "Past week",
}
SEARCH_TIME_BY_LABEL = {value.lower(): key for key, value in SEARCH_TIME_LABELS.items()}
SEARCH_SORT_LABELS = {"relevance": "Relevance"}
SEARCH_SORT_BY_LABEL = {value.lower(): key for key, value in SEARCH_SORT_LABELS.items()}
DEFAULT_SEARCH_ENV_CONCURRENCY = 3
DEFAULT_SEARCH_QUERIES_PER_ENV = 2
ADSPOWER_API_MIN_INTERVAL_SECONDS = 1.1
ADSPOWER_BROWSER_START_RETRIES = 4

_adspower_api_lock = threading.Lock()
_adspower_last_api_call_at = 0.0


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


@dataclass
class AdsPowerSettings:
    api_url: str
    api_key: str
    user_id: str
    target_url: str = "https://www.reddit.com"
    env_id: str = "env-1"


class HumanMouse:
    def __init__(self) -> None:
        self.current_x = random.randint(100, 500)
        self.current_y = random.randint(100, 300)

    def move_to_element(self, page: Page, locator: Locator) -> None:
        locator.wait_for(state="visible", timeout=10000)
        locator.scroll_into_view_if_needed()
        time.sleep(random.uniform(0.35, 0.9))

        box = locator.bounding_box()
        if not box:
            raise RuntimeError("unable_to_get_bounding_box")

        target_x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
        target_y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
        distance = math.hypot(target_x - self.current_x, target_y - self.current_y)
        steps = min(80, max(12, int(distance / 7)))

        for index in range(steps + 1):
            ratio = index / steps
            eased = ratio * (2 - ratio)
            x = self.current_x + (target_x - self.current_x) * eased + random.uniform(-0.8, 0.8)
            y = self.current_y + (target_y - self.current_y) * eased + random.uniform(-0.8, 0.8)
            page.mouse.move(x, y)
            time.sleep(random.uniform(0.002, 0.007))

        self.current_x, self.current_y = target_x, target_y

    def human_click(self, page: Page, locator: Locator) -> None:
        self.move_to_element(page, locator)
        time.sleep(random.uniform(0.08, 0.22))
        page.mouse.down()
        time.sleep(random.uniform(0.04, 0.12))
        page.mouse.up()


class RedditSearchRunner:
    def __init__(self, settings: AdsPowerSettings) -> None:
        self.settings = settings
        self.mouse = HumanMouse()
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
        self._stop_browser()

    def collect_query(
        self,
        query: str,
        *,
        search_time: str,
        target_count: int,
    ) -> QuerySearchResult:
        if self._context is None:
            raise RuntimeError("browser_context_not_initialized")

        page = self._context.new_page()
        try:
            search_results_url = self._run_search_flow_to_search_page(page, query)
            self._align_search_filters(page, search_time=search_time)
            search_results_url = page.url
            results = self._collect_search_results_until_target(page, query, target_count)
            if not results and self._is_no_results_page(page):
                return QuerySearchResult("no_results", "no_results", search_results_url, [], 0)
            status = "success" if results else "failed"
            reason = "" if results else "no_search_results_collected"
            return QuerySearchResult(status, reason, search_results_url, results, len(results))
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

            if "too many request" not in last_error.lower() or attempt >= ADSPOWER_BROWSER_START_RETRIES - 1:
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

    def _run_search_flow_to_search_page(self, page: Page, query: str) -> str:
        page.goto(self.settings.target_url, wait_until="domcontentloaded")
        self._settle_page(page)
        search_input = self._locate_search_input(page)
        if search_input is None:
            raise RuntimeError("search_input_not_found")

        self.mouse.human_click(page, search_input)
        time.sleep(random.uniform(0.4, 1.0))
        page.keyboard.press("Control+A")
        time.sleep(0.08)
        page.keyboard.press("Backspace")
        time.sleep(0.12)
        self._human_typing(page, query)
        time.sleep(random.uniform(0.5, 1.2))
        page.keyboard.press("Enter")

        final_url = self._wait_for_search_results(page)
        self._settle_page(page)
        return final_url

    def _settle_page(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except PlaywrightTimeoutError:
            pass
        time.sleep(random.uniform(0.8, 1.4))

    def _locate_search_input(self, page: Page) -> Locator | None:
        candidates = [
            page.get_by_placeholder("Find anything"),
            page.locator("reddit-search-large input[name='q']"),
            page.locator("input[name='q']"),
        ]
        for candidate_group in candidates:
            locator = self._first_visible(candidate_group)
            if locator is not None:
                return locator
        return None

    def _first_visible(self, locator_group: Locator) -> Locator | None:
        try:
            count = locator_group.count()
        except PlaywrightError:
            return None
        for index in range(count):
            candidate = locator_group.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except PlaywrightError:
                continue
        return None

    def _human_typing(self, page: Page, text: str) -> None:
        for index, char in enumerate(text):
            if 0 < index < len(text) - 1 and random.random() < 0.035:
                page.keyboard.type(random.choice("abcdefghijklmnopqrstuvwxyz"))
                time.sleep(random.uniform(0.08, 0.2))
                page.keyboard.press("Backspace")
                time.sleep(random.uniform(0.18, 0.35))
            page.keyboard.type(char)
            time.sleep(random.uniform(0.045, 0.14))

    def _wait_for_search_results(self, page: Page) -> str:
        deadline = time.time() + 12.0
        last_url = page.url
        while time.time() < deadline:
            current_url = page.url
            last_url = current_url
            if current_url.startswith(DEFAULT_SEARCH_URL_PREFIX):
                return current_url
            time.sleep(0.1)
        raise RuntimeError(f"search_results_url_not_reached:{last_url}")

    def _align_search_filters(self, page: Page, *, search_time: str) -> None:
        self._align_single_filter(page, kind="sort", target_token="relevance")
        self._align_single_filter(page, kind="time", target_token=search_time)

    def _align_single_filter(self, page: Page, *, kind: str, target_token: str) -> None:
        current_filters = self._get_current_search_filters(page)
        if current_filters[kind] == target_token:
            return

        target_label = self._filter_label(kind, target_token)
        button = self._find_filter_button(page, kind)
        if button is None:
            raise RuntimeError(f"{kind}_filter_button_not_found")

        before_url = page.url
        self.mouse.human_click(page, button)
        time.sleep(random.uniform(0.2, 0.5))
        option = self._find_visible_text_candidate(page, target_label)
        if option is None:
            raise RuntimeError(f"{kind}_filter_option_not_found:{target_label}")
        self.mouse.human_click(page, option)
        self._wait_for_filter_application(page, before_url)

        current_filters = self._get_current_search_filters(page)
        if current_filters[kind] != target_token:
            raise RuntimeError(f"{kind}_filter_not_applied:{target_token}")

    def _get_current_search_filters(self, page: Page) -> dict[str, str]:
        result = {"sort": "", "time": ""}
        selectors = {
            "sort": "search-sort-dropdown-menu#search_modifier_post_sort",
            "time": "search-sort-dropdown-menu#search_modifier_time_range",
        }
        mappings = {"sort": SEARCH_SORT_BY_LABEL, "time": SEARCH_TIME_BY_LABEL}
        for kind, selector in selectors.items():
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=4000)
                text = locator.evaluate(
                    """(el) => {
                        const trigger = el.querySelector("[slot='trigger-content']");
                        return (trigger?.innerText || trigger?.textContent || "").trim();
                    }"""
                )
            except Exception:
                text = ""
            result[kind] = mappings[kind].get(_normalize_visible_text(str(text or "")).lower(), "")
        return result

    def _find_filter_button(self, page: Page, kind: str) -> Locator | None:
        selector = (
            "search-sort-dropdown-menu#search_modifier_post_sort"
            if kind == "sort"
            else "search-sort-dropdown-menu#search_modifier_time_range"
        )
        candidate = page.locator(selector).first
        try:
            candidate.wait_for(state="visible", timeout=4000)
            return candidate
        except Exception:
            return None

    def _find_visible_text_candidate(self, page: Page, target_text: str) -> Locator | None:
        locator = page.get_by_text(target_text, exact=True)
        try:
            count = locator.count()
        except Exception:
            return None
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    def _wait_for_filter_application(self, page: Page, before_url: str) -> None:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if page.url != before_url:
                self._settle_page(page)
                time.sleep(random.uniform(1.2, 2.2))
                return
            time.sleep(0.1)
        self._settle_page(page)
        time.sleep(random.uniform(1.2, 2.2))

    def _filter_label(self, kind: str, token: str) -> str:
        mapping = SEARCH_SORT_LABELS if kind == "sort" else SEARCH_TIME_LABELS
        label = mapping.get(token)
        if not label:
            raise RuntimeError(f"unsupported_{kind}_filter:{token}")
        return label

    def _collect_search_results_until_target(self, page: Page, query: str, target_count: int) -> list[RawSearchResult]:
        seen_items: dict[str, RawSearchResult] = {}
        no_growth_rounds = 0
        max_scroll_rounds = 8

        for scroll_round in range(max_scroll_rounds + 1):
            if self._is_no_results_page(page):
                return []

            round_items = self._collect_search_results(page, query, max_results=max(target_count * 3, 80))
            new_count = 0
            for item in round_items:
                normalized_url = normalize_post_url(item.post_url)
                if not normalized_url or normalized_url in seen_items:
                    continue
                item.post_url = normalized_url
                item.result_index = len(seen_items) + 1
                seen_items[normalized_url] = item
                new_count += 1

            if len(seen_items) >= target_count:
                return list(seen_items.values())[:target_count]
            if scroll_round >= max_scroll_rounds:
                return list(seen_items.values())
            if new_count == 0:
                no_growth_rounds += 1
                if seen_items and no_growth_rounds >= 3:
                    return list(seen_items.values())
            else:
                no_growth_rounds = 0
            self._perform_search_results_scroll(page)
            self._settle_page(page)
        return list(seen_items.values())

    def _collect_search_results(self, page: Page, query: str, max_results: int) -> list[RawSearchResult]:
        raw_items = page.evaluate(
            """
            (maxResults) => {
                const text = (el) => el ? (el.innerText || el.textContent || "").trim() : "";
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
                const anchors = Array.from(document.querySelectorAll("a[data-testid='post-title-text'], a[data-testid='post-title']"));
                const items = [];
                const seen = new Set();

                for (const anchor of anchors) {
                    if (items.length >= maxResults) break;
                    const href = normalizeUrl(anchor.getAttribute("href") || "");
                    if (!href || seen.has(href) || !/\\/comments\\//i.test(href)) continue;

                    let node = anchor;
                    let candidate = null;
                    for (let depth = 0; depth < 10 && node; depth += 1) {
                        const rawText = text(node);
                        const lowered = rawText.toLowerCase();
                        const subredditEl =
                            node.querySelector("a[aria-haspopup='dialog'][href^='/r/']:not([href*='/comments/']) .truncate") ||
                            node.querySelector("a[href^='/r/']:not([href*='/comments/']) .truncate") ||
                            node.querySelector("a[aria-haspopup='dialog'][href^='/r/']:not([href*='/comments/'])") ||
                            node.querySelector("a[href^='/r/']:not([href*='/comments/'])");
                        const subredditText = text(subredditEl).split(/\\s+/)[0];
                        const timeEl = node.querySelector("time");
                        const counterRow = node.querySelector("[data-testid='search-counter-row']");
                        const numberNodes = counterRow ? Array.from(counterRow.querySelectorAll("faceplate-number[number]")) : [];
                        const title =
                            text(node.querySelector("a[data-testid='post-title-text']")) ||
                            anchor.getAttribute("aria-label") ||
                            text(anchor);
                        const invalid =
                            lowered.includes("people also search for")
                            || lowered.includes("view answers")
                            || /^answers\\b/i.test(lowered)
                            || /^posts\\b/i.test(lowered);
                        if (/^r\\//i.test(subredditText) && !invalid && title) {
                            candidate = {
                                post_url: href,
                                title,
                                subreddit: subredditText,
                                age_text: timeEl ? text(timeEl) : "",
                                votes: numberNodes[0] ? parseCount(numberNodes[0].getAttribute("number") || text(numberNodes[0])) : null,
                                comments: numberNodes[1] ? parseCount(numberNodes[1].getAttribute("number") || text(numberNodes[1])) : null,
                                raw_text: rawText,
                            };
                        }
                        node = node.parentElement;
                    }
                    if (!candidate || !candidate.post_url || !candidate.title || !candidate.subreddit) continue;
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

    def _perform_search_results_scroll(self, page: Page) -> None:
        viewport_height = int(
            page.evaluate("() => Math.max(window.innerHeight || 0, document.documentElement.clientHeight || 0, 700)")
        )
        target_distance = max(260, int(viewport_height * random.uniform(0.92, 1.1)))
        chunks = max(3, min(8, int(target_distance / 220)))
        travelled = 0
        for index in range(chunks):
            remaining = target_distance - travelled
            if remaining <= 0:
                break
            delta = remaining if index == chunks - 1 else min(remaining, random.randint(90, 240))
            page.mouse.wheel(0, delta)
            travelled += delta
            time.sleep(random.uniform(0.08, 0.18))
        time.sleep(random.uniform(1.0, 1.8))

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


def run_reddit_search_batch(payload: RedditSearchRequest):
    profiles = load_adspower_search_profiles()
    deduper = SearchResultDeduper()
    query_results: list[dict[str, Any]] = []
    query_assignments = list(enumerate(payload.queries, start=1))

    requested_concurrency = _load_search_env_concurrency()
    queries_per_env = _load_search_queries_per_env()
    needed_environment_count = (len(query_assignments) + queries_per_env - 1) // queries_per_env
    profiles = profiles[: min(needed_environment_count, requested_concurrency, len(profiles), len(query_assignments))]
    chunks = _chunk_evenly(query_assignments, len(profiles))
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    threads: list[threading.Thread] = []

    for environment_index, (settings, chunk) in enumerate(zip(profiles, chunks, strict=False), start=1):
        thread = threading.Thread(
            target=_run_search_environment_worker,
            args=(settings, environment_index, chunk, payload.perQueryLimit, event_queue),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    completed_workers = 0
    pending_results: dict[int, dict[str, Any]] = {}
    next_query_index = 1

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
                    result=query_event["result"],
                )
                query_results.append(query_payload)
                yield query_payload
                next_query_index += 1
            continue
        yield event

    summary = build_summary(payload, query_results, deduper.results)
    yield {
        "type": "summary",
        "summary": summary.model_dump(),
        "results": [item.model_dump() for item in deduper.sorted_results()],
    }
    yield {
        "type": "done",
        "summary": summary.model_dump(),
        "results": [item.model_dump() for item in deduper.sorted_results()],
    }


def _run_search_environment_worker(
    settings: AdsPowerSettings,
    environment_index: int,
    assignments: list[tuple[int, PlannedQuery]],
    target_count: int,
    event_queue: queue.Queue[dict[str, Any] | None],
) -> None:
    processed_count = 0
    try:
        with RedditSearchRunner(settings) as runner:
            for query_index, query in assignments:
                event_queue.put(
                    {
                        "type": "query_started",
                        "queryIndex": query_index,
                        "query": query.query,
                        "timeRange": query.suggestedTimeRange,
                        "environmentId": settings.env_id,
                        "environmentIndex": environment_index,
                    }
                )
                result = runner.collect_query(
                    query.query,
                    search_time=query.suggestedTimeRange,
                    target_count=target_count,
                )
                event_queue.put(
                    {
                        "type": "_query_result_internal",
                        "queryIndex": query_index,
                        "query": query,
                        "result": result,
                    }
                )
                processed_count += 1
    except Exception as exc:
        for query_index, query in assignments[processed_count:]:
            event_queue.put(
                {
                    "type": "query_started",
                    "queryIndex": query_index,
                    "query": query.query,
                    "timeRange": query.suggestedTimeRange,
                    "environmentId": settings.env_id,
                    "environmentIndex": environment_index,
                }
            )
            event_queue.put(
                {
                    "type": "_query_result_internal",
                    "queryIndex": query_index,
                    "query": query,
                    "result": QuerySearchResult("failed", f"环境启动或执行失败: {exc}", "", [], 0),
                }
            )
    finally:
        event_queue.put(None)


def _build_query_result_payload(
    *,
    deduper: "SearchResultDeduper",
    query_index: int,
    query: PlannedQuery,
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
        "status": result.status,
        "reason": result.reason,
        "searchResultsUrl": result.search_results_url,
        "rawResultCount": len(result.results),
        "uniqueResultCount": sum(1 for item in raw_items if not item.duplicateOfQuery),
        "results": [item.model_dump() for item in raw_items],
    }


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
    if "reddit.com" not in host:
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
