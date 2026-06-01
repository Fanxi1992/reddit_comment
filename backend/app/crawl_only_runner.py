import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit
from uuid import uuid4

from app.comment_decision_runner import (
    DetailEnvironmentRunner,
    _chunk_evenly,
    _load_detail_concurrency,
    _load_detail_urls_per_env,
    load_adspower_profiles,
)
from app.reddit_searcher import extract_post_id, normalize_post_url, run_reddit_search_batch
from app.schemas import (
    CrawlOnlyRequest,
    PlannedQuery,
    QueryPlanGenerateRequest,
    RedditSearchRequest,
    RedditSearchResultItem,
)


OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "data" / "crawl_outputs"
MAX_CRAWL_ONLY_POSTS = 120


def run_crawl_only_stream(payload: CrawlOnlyRequest, stop_event: threading.Event | None = None) -> Iterator[dict[str, Any]]:
    stop_event = stop_event or threading.Event()
    search_results: list[RedditSearchResultItem] = []
    search_metadata: dict[str, Any] = {}

    yield {
        "type": "crawl_started",
        "source": payload.source,
        "maxCommentsPerPost": payload.maxCommentsPerPost,
        "perQueryLimit": payload.perQueryLimit,
    }

    if payload.source == "simulated_search":
        yield {
            "type": "search_started",
            "totalQueries": len(payload.queries or []),
            "perQueryLimit": payload.perQueryLimit,
            "searchSort": "relevance",
        }
        search_results, search_metadata = yield from _run_simulated_search(payload, stop_event)
    else:
        search_results = _build_manual_search_results([str(url) for url in payload.urls or []])
        search_metadata = {
            "summary": {
                "totalQueries": 1,
                "successfulQueries": 1 if search_results else 0,
                "failedQueries": 0 if search_results else 1,
                "rawUrlCount": len(payload.urls or []),
                "uniqueUrlCount": len(search_results),
            },
            "results": [item.model_dump() for item in search_results],
        }

    if stop_event.is_set():
        return

    deduped_results = _dedupe_search_results(search_results)
    limited_results = deduped_results[:MAX_CRAWL_ONLY_POSTS]
    search_metadata = _build_locked_search_metadata(search_metadata, limited_results, len(deduped_results))
    yield {"type": "search_completed", **search_metadata}

    if not limited_results:
        yield {"type": "error", "message": "没有可抓取的去重 Reddit URL"}
        return

    detail_results = yield from _run_detail_crawl(payload, limited_results, search_metadata, stop_event)
    if stop_event.is_set():
        return

    summary = _build_summary(len(limited_results), detail_results)
    artifact_id, json_path, markdown_path = write_crawl_only_artifacts(
        payload=payload,
        search_metadata=search_metadata,
        results=detail_results,
        summary=summary,
    )
    artifact_payload = {
        "artifactId": artifact_id,
        "jsonPath": str(json_path),
        "markdownPath": str(markdown_path),
    }
    yield {"type": "artifact_ready", **artifact_payload}
    yield {
        "type": "done",
        "summary": summary,
        "results": detail_results,
        **artifact_payload,
    }


def _run_simulated_search(
    payload: CrawlOnlyRequest,
    stop_event: threading.Event,
) -> Iterator[dict[str, Any] | tuple[list[RedditSearchResultItem], dict[str, Any]]]:
    request = RedditSearchRequest(
        productContext=_dummy_product_context(),
        queries=payload.queries or [],
        perQueryLimit=payload.perQueryLimit,
        searchSort="relevance",
    )
    final_results: list[RedditSearchResultItem] = []
    metadata: dict[str, Any] = {}

    for event in run_reddit_search_batch(request, stop_event=stop_event):
        if stop_event.is_set():
            break
        event_type = event.get("type")
        if event_type in {"query_started", "query_result", "error"}:
            yield event
        if event_type == "done":
            raw_results = event.get("results") or []
            final_results = [RedditSearchResultItem.model_validate(item) for item in raw_results]
            metadata = {
                "summary": event.get("summary") or {},
                "results": raw_results,
            }
            break

    return final_results, metadata


def _run_detail_crawl(
    payload: CrawlOnlyRequest,
    search_results: list[RedditSearchResultItem],
    search_metadata: dict[str, Any],
    stop_event: threading.Event,
) -> Iterator[dict[str, Any] | list[dict[str, Any]]]:
    profiles = load_adspower_profiles()
    requested_concurrency = _load_detail_concurrency()
    urls_per_env = _load_detail_urls_per_env()
    needed_environment_count = (len(search_results) + urls_per_env - 1) // urls_per_env
    selected_profile_count = min(needed_environment_count, requested_concurrency, len(profiles), len(search_results))
    if selected_profile_count <= 0:
        detail_results = [
            _build_failed_result(item, "没有可用的 AdsPower 环境", environment_id=None)
            for item in search_results
        ]
        for result in detail_results:
            yield {"type": "post_result", "environmentId": None, "result": result}
        return detail_results

    profiles = profiles[:selected_profile_count]
    chunks = _chunk_evenly(search_results, len(profiles))
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    threads: list[threading.Thread] = []

    for index, (profile, chunk) in enumerate(zip(profiles, chunks, strict=False), start=1):
        thread = threading.Thread(
            target=_run_crawl_environment_worker,
            args=(profile, index, chunk, payload.maxCommentsPerPost, event_queue, stop_event),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    completed_workers = 0
    detail_results: list[dict[str, Any]] = []
    completed_normally = False
    try:
        while completed_workers < len(threads):
            event = event_queue.get()
            if event is None:
                completed_workers += 1
                continue
            if event.get("type") == "post_result":
                detail_results.append(event.get("result") or {})
            yield event
        completed_normally = True
    finally:
        if not completed_normally:
            stop_event.set()
        for thread in threads:
            thread.join()

    detail_results.sort(key=lambda item: int((item.get("searchResult") or {}).get("resultIndex") or 0))
    return detail_results


def _run_crawl_environment_worker(
    profile,
    environment_index: int,
    items: list[RedditSearchResultItem],
    max_comments_per_post: int,
    event_queue: queue.Queue[dict[str, Any] | None],
    stop_event: threading.Event,
) -> None:
    env_payload = {
        "environmentId": profile.env_id,
        "environmentIndex": environment_index,
        "userId": profile.user_id,
        "totalPosts": len(items),
    }
    event_queue.put({"type": "environment_started", **env_payload})
    pending_items = {item.postUrl: item for item in items}
    try:
        with DetailEnvironmentRunner(profile, max_comments_per_post=max_comments_per_post) as runner:
            for item in items:
                if stop_event.is_set():
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
                status = "success" if detail.get("status") == "success" else "failed"
                result = {
                    "postUrl": item.postUrl,
                    "title": detail.get("title") or item.title,
                    "subreddit": detail.get("subreddit") or item.subreddit,
                    "status": status,
                    "reason": detail.get("reason") if status != "success" else None,
                    "environmentId": profile.env_id,
                    "searchResult": item.model_dump(),
                    "detail": detail,
                }
                pending_items.pop(item.postUrl, None)
                event_queue.put({"type": "post_result", "environmentId": profile.env_id, "result": result})
    except Exception as exc:
        for item in pending_items.values():
            event_queue.put(
                {
                    "type": "post_result",
                    "environmentId": profile.env_id,
                    "result": _build_failed_result(item, f"环境启动或执行失败: {exc}", environment_id=profile.env_id),
                }
            )
    finally:
        event_queue.put({"type": "environment_finished", **env_payload})
        event_queue.put(None)


def write_crawl_only_artifacts(
    *,
    payload: CrawlOnlyRequest,
    search_metadata: dict[str, Any],
    results: list[dict[str, Any]],
    summary: dict[str, int],
) -> tuple[str, Path, Path]:
    artifact_id = uuid4().hex
    output_dir = OUTPUT_ROOT / time.strftime("%Y-%m-%d", time.localtime())
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{artifact_id}.json"
    markdown_path = output_dir / f"{artifact_id}.md"
    output_payload = {
        "artifactId": artifact_id,
        "source": payload.source,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "maxCommentsPerPost": payload.maxCommentsPerPost,
        "perQueryLimit": payload.perQueryLimit,
        "search": search_metadata,
        "summary": summary,
        "results": results,
    }
    json_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_crawl_only_markdown(output_payload), encoding="utf-8")
    return artifact_id, json_path, markdown_path


def resolve_crawl_only_artifact_path(artifact_id: str, kind: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
        raise FileNotFoundError("artifact_not_found")
    suffix = ".md" if kind == "markdown" else ".json"
    root = OUTPUT_ROOT.resolve()
    for candidate in root.glob(f"*/{artifact_id}{suffix}"):
        resolved = candidate.resolve()
        if root in resolved.parents and resolved.is_file():
            return resolved
    raise FileNotFoundError("artifact_not_found")


def render_crawl_only_markdown(payload: dict[str, Any]) -> str:
    source_label = "模拟搜索（仅抓取）" if payload.get("source") == "simulated_search" else "手动导入 URL（仅抓取）"
    summary = payload.get("summary") or {}
    sections = [
        "# Reddit 语料抓取结果",
        "",
        f"任务类型：{source_label}",
        f"抓取时间：{payload.get('createdAt', '')}",
        f"每帖评论上限：{payload.get('maxCommentsPerPost', '')}",
        f"总帖子数：{summary.get('totalPosts', 0)}",
        f"成功：{summary.get('successCount', 0)}",
        f"失败：{summary.get('failedCount', 0)}",
        f"跳过：{summary.get('skippedCount', 0)}",
        "",
    ]
    search_results_url = _first_search_results_url(payload)
    if search_results_url:
        sections.extend([f"搜索结果页：{search_results_url}", ""])

    for index, item in enumerate(payload.get("results") or [], start=1):
        sections.extend(_render_post_markdown(index, item))

    return "\n".join(sections).rstrip() + "\n"


def _render_post_markdown(index: int, item: dict[str, Any]) -> list[str]:
    search_result = item.get("searchResult") or {}
    detail = item.get("detail") or {}
    title = _preferred_text(detail.get("title"), item.get("title"), search_result.get("title"))
    subreddit = _preferred_text(detail.get("subreddit"), item.get("subreddit"), search_result.get("subreddit"))
    post_url = _preferred_text(detail.get("post_url"), item.get("postUrl"), search_result.get("postUrl"))
    body_text = str(detail.get("body_text") or "").strip()
    media_urls = [str(url).strip() for url in (detail.get("media_urls") or []) if str(url).strip()]
    outbound_url = str(detail.get("outbound_url") or "").strip()
    lines = [
        f"## Post {index}",
        "",
        "下面是post的详细内容以及对应评论区的部分内容：",
        f"所在社区：{subreddit or 'unknown'}",
        f"标题：{title or 'unknown'}",
        f"帖子链接：{post_url or 'unknown'}",
    ]
    if media_urls:
        lines.append("媒体链接：")
        lines.extend(f"- {url}" for url in media_urls)
    if outbound_url:
        lines.append(f"外链地址：{outbound_url}")
    lines.append("正文：")
    lines.extend(_render_body_lines(body_text))
    lines.append(f"点赞数：{_display_count(_preferred_count(detail.get('upvotes'), search_result.get('votes')))}")
    lines.append(f"评论数：{_display_count(_preferred_count(detail.get('comments'), search_result.get('comments')))}")
    lines.append("")

    if item.get("status") != "success":
        lines.append(f"详情抓取失败：{str(item.get('reason') or 'unknown_error').strip()}")
        lines.append("")
        return lines

    lines.append("该POST的评论区部分内容如下：")
    lines.append("")
    comment_roots = ((detail.get("comment_tree") or {}).get("comments") or [])
    if not comment_roots:
        lines.append("（当前未获取到评论内容）")
        lines.append("")
        return lines
    for comment_index, root in enumerate(comment_roots, start=1):
        lines.append(f"Comment Tree {comment_index}：")
        lines.append("")
        lines.extend(_render_comment_subtree(root, parent=None, indent_level=0))
        lines.append("")
    return lines


def _render_comment_subtree(node: dict[str, Any], parent: dict[str, Any] | None, indent_level: int) -> list[str]:
    indent = "\t" * indent_level
    author = _format_author_label(node)
    text = _normalize_comment_text(str(node.get("text") or ""))
    if parent is None:
        lines = [f"{indent}{author}：{text}"]
    else:
        lines = [f"{indent}{author} reply to {_format_author_label(parent)}：{text}"]
    for reply in node.get("replies") or []:
        lines.extend(_render_comment_subtree(reply, parent=node, indent_level=indent_level + 1))
    return lines


def _build_manual_search_results(urls: list[str]) -> list[RedditSearchResultItem]:
    output: list[RedditSearchResultItem] = []
    seen: set[str] = set()
    for url in urls:
        normalized_url = normalize_post_url(url)
        if not normalized_url or normalized_url in seen:
            continue
        seen.add(normalized_url)
        output.append(
            RedditSearchResultItem(
                query="manual_url_upload",
                queryIntent="other",
                priority=1,
                timeRange="all",
                resultIndex=len(output) + 1,
                postUrl=normalized_url,
                postId=extract_post_id(normalized_url),
                title=f"Manual Reddit URL #{len(output) + 1}",
                subreddit=_extract_subreddit(normalized_url) or "unknown",
                ageText="",
                votes=None,
                comments=None,
                duplicateOfQuery=None,
                matchedQueries=["manual_url_upload"],
            )
        )
    return output


def _dedupe_search_results(items: list[RedditSearchResultItem]) -> list[RedditSearchResultItem]:
    seen: set[str] = set()
    output: list[RedditSearchResultItem] = []
    for item in items:
        normalized_url = normalize_post_url(item.postUrl)
        if not normalized_url or normalized_url in seen:
            continue
        seen.add(normalized_url)
        item.postUrl = normalized_url
        item.resultIndex = len(output) + 1
        output.append(item)
    return output


def _build_locked_search_metadata(
    search_metadata: dict[str, Any],
    results: list[RedditSearchResultItem],
    unique_before_limit: int,
) -> dict[str, Any]:
    summary = dict(search_metadata.get("summary") or {})
    summary["uniqueUrlCount"] = len(results)
    summary["lockedUrlCount"] = len(results)
    summary["eligibleUniqueUrlCount"] = unique_before_limit
    summary["droppedByLimitCount"] = max(0, unique_before_limit - len(results))
    return {
        **search_metadata,
        "summary": summary,
        "results": [item.model_dump() for item in results],
    }


def _build_failed_result(item: RedditSearchResultItem, reason: str, environment_id: str | None) -> dict[str, Any]:
    return {
        "postUrl": item.postUrl,
        "title": item.title,
        "subreddit": item.subreddit,
        "status": "failed",
        "reason": reason,
        "environmentId": environment_id,
        "searchResult": item.model_dump(),
        "detail": {},
    }


def _dummy_product_context() -> QueryPlanGenerateRequest:
    return QueryPlanGenerateRequest(
        productName="crawl-only",
        productDescription="Manual simulated search crawl-only task",
        desiredQueryCount=1,
    )


def _build_summary(total_posts: int, results: list[dict[str, Any]]) -> dict[str, int]:
    success = sum(1 for item in results if item.get("status") == "success")
    skipped = sum(1 for item in results if item.get("status") == "skipped")
    failed = sum(1 for item in results if item.get("status") == "failed")
    return {
        "totalPosts": total_posts,
        "processedPosts": len(results),
        "successCount": success,
        "skippedCount": skipped,
        "failedCount": failed,
    }


def _extract_subreddit(url: str) -> str:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    for index, part in enumerate(parts):
        if part.lower() == "r" and index + 1 < len(parts):
            return f"r/{parts[index + 1]}"
    return ""


def _first_search_results_url(payload: dict[str, Any]) -> str:
    for item in ((payload.get("search") or {}).get("results") or []):
        value = item.get("searchResultsUrl") or item.get("search_results_url")
        if value:
            return str(value)
    return ""


def _preferred_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _preferred_count(primary: Any, fallback: Any) -> Any:
    return primary if primary is not None else fallback


def _display_count(value: Any) -> str:
    return "unknown" if value is None or value == "" else str(value)


def _render_body_lines(body_text: str) -> list[str]:
    normalized = body_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized.split("\n") if normalized else ["（无正文）"]


def _format_author_label(node: dict[str, Any]) -> str:
    author = str(node.get("author") or "unknown").strip() or "unknown"
    return f"{author}（OP）" if node.get("is_op") else author


def _normalize_comment_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip() or "（空评论）"


def encode_ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
