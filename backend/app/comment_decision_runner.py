import json
import logging
import os
import queue
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from app.comment_decider import generate_comment_decision
from app.reddit_detail_collector import LoadedCommentTreeExtractor, PostDetailObservationCollector
from app.reddit_searcher import normalize_post_url
from app.schemas import CommentDecisionRequest, CommentLengthDistribution, RedditSearchResultItem


MAX_COMMENTS_PER_POST = 30
DEFAULT_DETAIL_ENV_CONCURRENCY = 3
DEFAULT_DETAIL_URLS_PER_ENV = 20
DETAIL_CONSECUTIVE_FAILURE_LIMIT = 3
ADSPOWER_API_MIN_INTERVAL_SECONDS = 1.1
ADSPOWER_BROWSER_START_RETRIES = 4

_adspower_api_lock = threading.Lock()
_adspower_last_api_call_at = 0.0

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class AdsPowerProfile:
    env_id: str
    api_url: str
    api_key: str
    user_id: str
    target_url: str = "https://www.reddit.com"


class DetailEnvironmentRunner:
    def __init__(self, profile: AdsPowerProfile) -> None:
        self.profile = profile
        self.detail_observer = PostDetailObservationCollector()
        self.comment_extractor = LoadedCommentTreeExtractor()
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "DetailEnvironmentRunner":
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
        if not self._stop_browser():
            logger.warning(
                "AdsPower detail environment stop failed: env_id=%s user_id=%s",
                self.profile.env_id,
                self.profile.user_id,
            )

    def collect_detail(self, item: RedditSearchResultItem) -> dict[str, Any]:
        if self._context is None:
            raise RuntimeError("browser_context_not_initialized")

        page = self._context.new_page()
        try:
            page.goto(item.postUrl, wait_until="commit", timeout=20000)
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            self._accept_mature_content_gate_if_present(page)
            self._wait_for_detail_post(page)
            time.sleep(random.uniform(0.8, 1.3))
            observation = self.detail_observer.collect(page, origin="comment_decision")
            comment_tree = self.comment_extractor.collect(page, total_comment_count=observation.comments or 0)
            limited_comment_tree = comment_tree.to_limited_dict(MAX_COMMENTS_PER_POST)
            return {
                "status": "success",
                "reason": "",
                "post_url": normalize_post_url(item.postUrl) or item.postUrl,
                "final_url": page.url,
                "title": observation.title,
                "subreddit": observation.subreddit,
                "author": observation.author,
                "flair": observation.flair,
                "post_type": observation.post_type,
                "body_text": observation.body_text,
                "body_length": observation.body_length,
                "media_urls": observation.media_urls,
                "outbound_url": observation.outbound_url,
                "upvotes": observation.upvotes,
                "comments": observation.comments,
                "comment_tree": limited_comment_tree,
                "loaded_comment_count": comment_tree.loaded_comment_count,
                "included_comment_count": int(limited_comment_tree.get("included_comment_count") or 0),
                "top_level_comment_count": comment_tree.top_level_count,
                "max_comment_depth": comment_tree.max_depth,
            }
        except Exception as exc:
            reason = self._build_detail_failure_reason(page, exc)
            return {
                "status": "failed",
                "reason": reason,
                "post_url": normalize_post_url(item.postUrl) or item.postUrl,
                "final_url": page.url if not page.is_closed() else "",
                "title": item.title,
                "subreddit": item.subreddit,
                "body_text": "",
                "body_length": 0,
                "media_urls": [],
                "comment_tree": {},
                "loaded_comment_count": 0,
                "included_comment_count": 0,
                "top_level_comment_count": 0,
                "max_comment_depth": 0,
            }
        finally:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass

    def _accept_mature_content_gate_if_present(self, page) -> None:
        try:
            has_gate = bool(
                page.evaluate(
                    """
                    () => {
                        const text = (document.body?.innerText || "").toLowerCase();
                        const hasButton = Array.from(document.querySelectorAll("button")).some((button) => {
                            const buttonText = (button.innerText || button.textContent || "").trim().toLowerCase();
                            return buttonText.includes("yes, i'm over 18") || buttonText.includes("yes, i’m over 18");
                        });
                        return hasButton && text.includes("mature content");
                    }
                    """
                )
            )
        except Exception:
            return
        if not has_gate:
            return

        clicked = self._click_mature_content_gate_button(page)
        if not clicked:
            return

        self._wait_after_mature_content_gate_click(page)

    def _wait_after_mature_content_gate_click(self, page) -> None:
        deadline = time.time() + 15.0
        while time.time() < deadline:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=1000)
            except Exception:
                pass
            try:
                gate_state = page.evaluate(
                    """
                    () => {
                        const bodyText = (document.body?.innerText || "").toLowerCase();
                        const over18ButtonCount = Array.from(document.querySelectorAll("button")).filter((button) => {
                            const text = (button.innerText || button.textContent || "").trim().toLowerCase();
                            return text.includes("yes, i'm over 18") || text.includes("yes, i’m over 18");
                        }).length;
                        return {
                            hasPost: document.querySelectorAll("shreddit-post").length > 0,
                            hasMatureGate: bodyText.includes("mature content") && over18ButtonCount > 0,
                        };
                    }
                    """
                )
            except Exception:
                gate_state = {}
            if gate_state.get("hasPost") or not gate_state.get("hasMatureGate"):
                break
            time.sleep(0.3)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        time.sleep(random.uniform(0.5, 1.0))

    def _click_mature_content_gate_button(self, page) -> bool:
        for pattern in ["Yes, I'm over 18", "Yes, I’m over 18"]:
            try:
                button = page.get_by_role("button", name=pattern).first
                if button.count() and button.is_visible(timeout=1000) and button.is_enabled(timeout=1000):
                    button.click(timeout=3000)
                    return True
            except Exception:
                pass
        try:
            box = page.evaluate(
                """
                () => {
                    const buttons = Array.from(document.querySelectorAll("button"));
                    const target = buttons.find((button) => {
                        const text = (button.innerText || button.textContent || "").trim().toLowerCase();
                        if (!text.includes("yes, i'm over 18") && !text.includes("yes, i’m over 18")) return false;
                        const style = window.getComputedStyle(button);
                        const rect = button.getBoundingClientRect();
                        return style.visibility !== "hidden"
                            && style.display !== "none"
                            && Number(style.opacity || "1") > 0
                            && rect.width > 0
                            && rect.height > 0
                            && !button.disabled;
                    });
                    if (!target) return null;
                    const rect = target.getBoundingClientRect();
                    target.scrollIntoView({block: "center", inline: "center"});
                    return {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        width: rect.width,
                        height: rect.height,
                    };
                }
                """
            )
            if box:
                target_x = float(box["x"]) + random.uniform(-max(1.0, float(box["width"]) * 0.12), max(1.0, float(box["width"]) * 0.12))
                target_y = float(box["y"]) + random.uniform(-max(1.0, float(box["height"]) * 0.18), max(1.0, float(box["height"]) * 0.18))
                page.mouse.move(target_x, target_y, steps=random.randint(8, 16))
                time.sleep(random.uniform(0.08, 0.2))
                page.mouse.down()
                time.sleep(random.uniform(0.05, 0.14))
                page.mouse.up()
                return True
        except Exception:
            pass
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                        const buttons = Array.from(document.querySelectorAll("button"));
                        const target = buttons.find((button) => {
                            const text = (button.innerText || button.textContent || "").trim().toLowerCase();
                            if (!text.includes("yes, i'm over 18") && !text.includes("yes, i’m over 18")) return false;
                            const style = window.getComputedStyle(button);
                            const rect = button.getBoundingClientRect();
                            return style.visibility !== "hidden"
                                && style.display !== "none"
                                && Number(style.opacity || "1") > 0
                                && rect.width > 0
                                && rect.height > 0
                                && !button.disabled;
                        });
                        if (!target) return false;
                        target.click();
                        return true;
                    }
                    """
                )
            )
        except Exception:
            return False

    def _build_detail_failure_reason(self, page, exc: Exception) -> str:
        diagnostics = self._build_detail_page_diagnostics(page)
        return f"{exc}; page_diagnostics={diagnostics}"

    def _build_detail_page_diagnostics(self, page) -> dict[str, Any]:
        if page.is_closed():
            return {"page_closed": True}
        try:
            return page.evaluate(
                """
                () => {
                    const bodyText = (document.body?.innerText || "");
                    const lowerText = bodyText.toLowerCase();
                    const buttons = Array.from(document.querySelectorAll("button"));
                    const over18Buttons = buttons.filter((button) => {
                        const text = (button.innerText || button.textContent || "").trim().toLowerCase();
                        return text.includes("yes, i'm over 18") || text.includes("yes, i’m over 18");
                    });
                    const nsfwButtons = buttons.filter((button) => {
                        const text = (button.innerText || button.textContent || "").trim().toLowerCase();
                        return text.includes("view nsfw content");
                    });
                    return {
                        url: window.location.href,
                        documentTitle: document.title || "",
                        hasMatureContentText: lowerText.includes("mature content"),
                        hasOver18Text: lowerText.includes("over 18"),
                        over18ButtonCount: over18Buttons.length,
                        nsfwButtonCount: nsfwButtons.length,
                        shredditPostCount: document.querySelectorAll("shreddit-post").length,
                        shredditCommentCount: document.querySelectorAll("shreddit-comment").length,
                        hasPostH1: Boolean(document.querySelector("shreddit-post h1")),
                        hasPostTitleAttr: Boolean(document.querySelector("shreddit-post")?.getAttribute("post-title")),
                        bodyTextPreview: bodyText.slice(0, 240),
                    };
                }
                """
            )
        except Exception as diag_exc:
            return {"diagnostics_failed": str(diag_exc)}

    def _wait_for_detail_post(self, page) -> None:
        post = page.locator("shreddit-post").first
        post.wait_for(state="visible", timeout=15000)
        if "/comments/" not in page.url:
            deadline = time.time() + 8.0
            while time.time() < deadline:
                if "/comments/" in page.url:
                    return
                time.sleep(0.1)
            raise RuntimeError(f"detail_url_not_reached:{page.url}")

    def _start_browser(self) -> str | None:
        url = f"{self.profile.api_url}/api/v1/browser/start"
        headers = {"Authorization": f"Bearer {self.profile.api_key}"}
        params = {"user_id": self.profile.user_id, "headless": 0}
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
        url = f"{self.profile.api_url}/api/v1/browser/stop"
        headers = {"Authorization": f"Bearer {self.profile.api_key}"}
        params = {"user_id": self.profile.user_id}
        try:
            response = _rate_limited_adspower_get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return False
        return payload.get("code") == 0


def run_comment_decision_stream(
    payload: CommentDecisionRequest, stop_event: threading.Event | None = None
) -> Iterator[dict[str, Any]]:
    profiles = load_adspower_profiles()
    search_results = _dedupe_search_results(payload.searchResults)
    if not search_results:
        raise RuntimeError("没有可处理的去重 Reddit URL")

    requested_concurrency = _load_detail_concurrency()
    urls_per_env = _load_detail_urls_per_env()
    needed_environment_count = (len(search_results) + urls_per_env - 1) // urls_per_env
    selected_profile_count = min(needed_environment_count, requested_concurrency, len(profiles), len(search_results))
    profiles = random.sample(profiles, selected_profile_count)
    chunks = _chunk_evenly(search_results, len(profiles))
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    stop_event = stop_event or threading.Event()
    success_budget = {"count": 0}
    success_lock = threading.Lock()
    threads: list[threading.Thread] = []

    yield {
        "type": "decision_started",
        "totalPosts": len(search_results),
        "environmentCount": len(profiles),
        "maxSuggestions": payload.maxSuggestions,
    }

    for index, (profile, chunk) in enumerate(zip(profiles, chunks, strict=False), start=1):
        thread = threading.Thread(
            target=_run_environment_worker,
            args=(payload, profile, index, chunk, event_queue, stop_event, success_budget, success_lock),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    completed_workers = 0
    summary = {
        "totalPosts": len(search_results),
        "processedPosts": 0,
        "successCount": 0,
        "skippedCount": 0,
        "failedCount": 0,
    }
    successful_results: list[dict[str, Any]] = []

    try:
        while completed_workers < len(threads):
            event = event_queue.get()
            if event is None:
                completed_workers += 1
                continue
            if event.get("type") == "post_result":
                result = event.get("result") or {}
                summary["processedPosts"] += 1
                if result.get("status") == "success":
                    summary["successCount"] += 1
                    successful_results.append(result)
                elif result.get("status") == "skipped":
                    summary["skippedCount"] += 1
                else:
                    summary["failedCount"] += 1
            yield event
        yield {"type": "done", "summary": summary, "results": successful_results}
    finally:
        stop_event.set()
        for thread in threads:
            thread.join()


def _run_environment_worker(
    payload: CommentDecisionRequest,
    profile: AdsPowerProfile,
    environment_index: int,
    items: list[RedditSearchResultItem],
    event_queue: queue.Queue[dict[str, Any] | None],
    stop_event: threading.Event,
    success_budget: dict[str, int],
    success_lock: threading.Lock,
) -> None:
    env_payload = {
        "environmentId": profile.env_id,
        "environmentIndex": environment_index,
        "userId": profile.user_id,
        "totalPosts": len(items),
    }
    counts = {"processed": 0, "success": 0, "skipped": 0, "failed": 0}
    consecutive_detail_failures = 0
    event_queue.put({"type": "environment_started", **env_payload})

    try:
        with DetailEnvironmentRunner(profile) as runner:
            for item_index, item in enumerate(items):
                if stop_event.is_set():
                    if _success_budget_reached(payload.maxSuggestions, success_budget, success_lock):
                        for remaining_item in items[item_index:]:
                            remaining_result = _make_result(
                                remaining_item,
                                "skipped",
                                "已达到最大建议数",
                                profile.env_id,
                            )
                            event_queue.put(
                                {"type": "post_result", "environmentId": profile.env_id, "result": remaining_result}
                            )
                            counts["processed"] += 1
                            counts["skipped"] += 1
                    break

                event_queue.put(
                    {
                        "type": "post_started",
                        "environmentId": profile.env_id,
                        "postUrl": item.postUrl,
                        "title": item.title,
                    }
                )
                detail = runner.collect_detail(item)
                if detail.get("status") != "success":
                    reason = str(detail.get("reason") or "详情抓取失败")
                    result = _make_result(item, "failed", reason, profile.env_id)
                    event_queue.put({"type": "post_result", "environmentId": profile.env_id, "result": result})
                    counts["processed"] += 1
                    counts["failed"] += 1
                    consecutive_detail_failures += 1
                    if consecutive_detail_failures >= DETAIL_CONSECUTIVE_FAILURE_LIMIT:
                        stop_reason = f"环境连续详情抓取失败 {DETAIL_CONSECUTIVE_FAILURE_LIMIT} 次，停止该环境后续处理: {reason}"
                        for remaining_item in items[item_index + 1 :]:
                            remaining_result = _make_result(remaining_item, "failed", stop_reason, profile.env_id)
                            event_queue.put(
                                {"type": "post_result", "environmentId": profile.env_id, "result": remaining_result}
                            )
                            counts["processed"] += 1
                            counts["failed"] += 1
                        break
                    else:
                        continue
                consecutive_detail_failures = 0

                event_queue.put(
                    {
                        "type": "detail_collected",
                        "environmentId": profile.env_id,
                        "postUrl": item.postUrl,
                        "title": detail.get("title") or item.title,
                        "subreddit": detail.get("subreddit") or item.subreddit,
                        "commentCount": detail.get("included_comment_count") or 0,
                        "mediaCount": len(detail.get("media_urls") or []),
                    }
                )
                event_queue.put({"type": "gemini_started", "environmentId": profile.env_id, "postUrl": item.postUrl})

                try:
                    comment_length_style = _choose_comment_length_style(payload.commentLengthDistribution)
                    decision = generate_comment_decision(
                        product_context=payload.productContext,
                        search_result=item,
                        detail=detail,
                        comment_length_style=comment_length_style,
                    )
                    result = _make_result(
                        item,
                        decision["status"],
                        decision.get("reason") or "",
                        profile.env_id,
                        title=detail.get("title") or item.title,
                        subreddit=detail.get("subreddit") or item.subreddit,
                        comment_url=decision.get("commentUrl"),
                        comment_text=decision.get("commentText"),
                        comment_length_style=comment_length_style,
                    )
                    result = _apply_success_budget(result, payload.maxSuggestions, success_budget, success_lock, stop_event)
                except Exception as exc:
                    result = _make_result(item, "failed", f"Gemini 评论决策失败: {exc}", profile.env_id)

                event_queue.put({"type": "post_result", "environmentId": profile.env_id, "result": result})
                counts["processed"] += 1
                if result["status"] == "success":
                    counts["success"] += 1
                elif result["status"] == "skipped":
                    counts["skipped"] += 1
                else:
                    counts["failed"] += 1
    except Exception as exc:
        for item in items[counts["processed"] :]:
            result = _make_result(item, "failed", f"环境启动或执行失败: {exc}", profile.env_id)
            event_queue.put({"type": "post_result", "environmentId": profile.env_id, "result": result})
            counts["processed"] += 1
            counts["failed"] += 1
    finally:
        event_queue.put({"type": "environment_finished", **env_payload, **counts})
        event_queue.put(None)


def _rate_limited_adspower_get(url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: int) -> requests.Response:
    global _adspower_last_api_call_at
    with _adspower_api_lock:
        elapsed = time.monotonic() - _adspower_last_api_call_at
        if elapsed < ADSPOWER_API_MIN_INTERVAL_SECONDS:
            time.sleep(ADSPOWER_API_MIN_INTERVAL_SECONDS - elapsed)
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        _adspower_last_api_call_at = time.monotonic()
        return response


def load_adspower_profiles() -> list[AdsPowerProfile]:
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
        AdsPowerProfile(
            env_id=f"env-{index}",
            api_url=api_url.rstrip("/"),
            api_key=api_key,
            user_id=user_id,
            target_url=target_url,
        )
        for index, user_id in enumerate(user_ids, start=1)
    ]


def _load_detail_concurrency() -> int:
    raw_value = os.getenv("DETAIL_ENV_CONCURRENCY", "").strip()
    if not raw_value:
        return DEFAULT_DETAIL_ENV_CONCURRENCY
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_DETAIL_ENV_CONCURRENCY


def _load_detail_urls_per_env() -> int:
    raw_value = os.getenv("DETAIL_URLS_PER_ENV", "").strip()
    if not raw_value:
        return DEFAULT_DETAIL_URLS_PER_ENV
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_DETAIL_URLS_PER_ENV


def _dedupe_search_results(items: list[RedditSearchResultItem]) -> list[RedditSearchResultItem]:
    seen: set[str] = set()
    output: list[RedditSearchResultItem] = []
    for item in items:
        normalized_url = normalize_post_url(item.postUrl)
        if not normalized_url or normalized_url in seen:
            continue
        seen.add(normalized_url)
        item.postUrl = normalized_url
        output.append(item)
    return output


def _chunk_evenly(items: list[RedditSearchResultItem], fanout: int) -> list[list[RedditSearchResultItem]]:
    if fanout <= 1:
        return [items]
    base = len(items) // fanout
    remainder = len(items) % fanout
    chunks: list[list[RedditSearchResultItem]] = []
    cursor = 0
    for index in range(fanout):
        size = base + (1 if index < remainder else 0)
        chunk = items[cursor : cursor + size]
        if chunk:
            chunks.append(chunk)
        cursor += size
    return chunks


def _make_result(
    item: RedditSearchResultItem,
    status: str,
    reason: str,
    environment_id: str,
    *,
    title: str | None = None,
    subreddit: str | None = None,
    comment_url: str | None = None,
    comment_text: str | None = None,
    comment_length_style: str | None = None,
) -> dict[str, Any]:
    return {
        "postUrl": item.postUrl,
        "sourceQuery": item.query,
        "subreddit": subreddit or item.subreddit,
        "title": title or item.title,
        "status": status,
        "reason": reason or None,
        "commentUrl": comment_url,
        "commentText": comment_text,
        "environmentId": environment_id,
        "commentLengthStyle": comment_length_style,
    }


def _choose_comment_length_style(distribution: CommentLengthDistribution) -> str:
    roll = random.randint(1, 100)
    if roll <= distribution.short:
        return "short"
    if roll <= distribution.short + distribution.medium:
        return "medium"
    return "long"


def _apply_success_budget(
    result: dict[str, Any],
    max_suggestions: int | None,
    success_budget: dict[str, int],
    success_lock: threading.Lock,
    stop_event: threading.Event,
) -> dict[str, Any]:
    if result.get("status") != "success" or not max_suggestions:
        return result
    with success_lock:
        if success_budget["count"] >= max_suggestions:
            return {
                **result,
                "status": "skipped",
                "reason": "已达到最大建议数",
                "commentUrl": None,
                "commentText": None,
            }
        success_budget["count"] += 1
        if success_budget["count"] >= max_suggestions:
            stop_event.set()
    return result


def _success_budget_reached(
    max_suggestions: int | None,
    success_budget: dict[str, int],
    success_lock: threading.Lock,
) -> bool:
    if not max_suggestions:
        return False
    with success_lock:
        return success_budget["count"] >= max_suggestions


def encode_ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
