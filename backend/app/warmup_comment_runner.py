import json
import os
import queue
import threading
from typing import Any, Iterator

from app.crawl_only_runner import build_manual_search_results, run_detail_crawl
from app.reddit_searcher import normalize_post_url
from app.schemas import (
    WarmupCollectRequest,
    WarmupCollectedPost,
    WarmupCommentRequest,
    WarmupCommentResult,
)
from app.warmup_comment_generator import generate_warmup_comments


MAX_COMMENTS_FOR_CONTEXT = 30
DEFAULT_GENERATION_CONCURRENCY = 3


def run_warmup_collection_stream(
    payload: WarmupCollectRequest,
    stop_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    stop_event = stop_event or threading.Event()
    search_results = build_manual_search_results([str(url) for url in payload.postUrls])
    post_indexes = {
        normalize_post_url(item.postUrl): item.resultIndex
        for item in search_results
    }
    collected_posts: list[WarmupCollectedPost] = []
    failed_posts = 0

    yield {
        "type": "collection_started",
        "totalPosts": len(search_results),
        "message": "开始批量读取 Reddit 帖子",
    }

    for event in run_detail_crawl(
        search_results=search_results,
        max_comments_per_post=MAX_COMMENTS_FOR_CONTEXT,
        stop_event=stop_event,
    ):
        if stop_event.is_set():
            break
        event_type = event.get("type")
        if event_type == "post_started":
            post_url = str(event.get("postUrl") or "")
            yield {
                "type": "post_collecting",
                "postIndex": _resolve_post_index(post_indexes, post_url),
                "postUrl": post_url,
                "message": "正在读取帖子正文、图片和评论",
            }
            continue
        if event_type != "post_result":
            continue

        raw_result = event.get("result") or {}
        post_url = str(raw_result.get("postUrl") or "")
        post_index = int((raw_result.get("searchResult") or {}).get("resultIndex") or 0)
        if post_index <= 0:
            post_index = _resolve_post_index(post_indexes, post_url)
        if raw_result.get("status") != "success":
            failed_posts += 1
            yield {
                "type": "post_failed",
                "postIndex": post_index,
                "postUrl": post_url,
                "message": str(raw_result.get("reason") or "帖子读取失败"),
            }
            continue

        post = _to_collected_post(post_index, raw_result)
        collected_posts.append(post)
        yield {
            "type": "post_collected",
            "postIndex": post_index,
            "postUrl": post.postUrl,
            "post": post.model_dump(),
            "message": "帖子读取完成",
        }

    collected_posts.sort(key=lambda item: item.postIndex)
    summary = {
        "totalPosts": len(search_results),
        "processedPosts": len(collected_posts) + failed_posts,
        "successfulPosts": len(collected_posts),
        "failedPosts": failed_posts,
    }
    if not stop_event.is_set():
        yield {
            "type": "done",
            "summary": summary,
            "posts": [post.model_dump() for post in collected_posts],
        }


def run_warmup_comment_stream(
    payload: WarmupCommentRequest,
    stop_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    stop_event = stop_event or threading.Event()
    posts = sorted(payload.posts, key=lambda item: item.postIndex)
    work_queue: queue.Queue[WarmupCollectedPost] = queue.Queue()
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    for post in posts:
        work_queue.put(post)

    worker_count = min(_load_generation_concurrency(), len(posts))
    workers = [
        threading.Thread(
            target=_run_generation_worker,
            args=(payload, work_queue, event_queue, stop_event),
            daemon=True,
        )
        for _ in range(worker_count)
    ]

    yield {
        "type": "task_started",
        "totalPosts": len(posts),
        "commentsPerPost": payload.commentsPerPost,
        "message": f"开始为 {len(posts)} 个帖子生成预热评论",
    }

    for worker in workers:
        worker.start()

    completed_workers = 0
    successful_posts = 0
    failed_posts = 0
    results: list[dict[str, Any]] = []
    try:
        while completed_workers < len(workers):
            event = event_queue.get()
            if event is None:
                completed_workers += 1
                continue
            if event.get("type") == "comment_generated":
                results.append(event["result"])
            elif event.get("type") == "post_completed":
                successful_posts += 1
            elif event.get("type") == "post_failed":
                failed_posts += 1
            yield event
    finally:
        for worker in workers:
            worker.join()

    results.sort(key=lambda item: (int(item["postIndex"]), int(item["commentIndex"])))
    if not stop_event.is_set():
        yield {
            "type": "done",
            "summary": {
                "totalPosts": len(posts),
                "processedPosts": successful_posts + failed_posts,
                "successfulPosts": successful_posts,
                "failedPosts": failed_posts,
                "commentsPerPost": payload.commentsPerPost,
                "generatedCommentCount": len(results),
            },
            "results": results,
        }


def _run_generation_worker(
    payload: WarmupCommentRequest,
    work_queue: queue.Queue[WarmupCollectedPost],
    event_queue: queue.Queue[dict[str, Any] | None],
    stop_event: threading.Event,
) -> None:
    try:
        while not stop_event.is_set():
            try:
                post = work_queue.get_nowait()
            except queue.Empty:
                return

            event_queue.put(
                {
                    "type": "generation_started",
                    "postIndex": post.postIndex,
                    "postUrl": post.postUrl,
                    "commentsPerPost": payload.commentsPerPost,
                    "message": "正在结合帖子、评论和图片生成",
                }
            )
            try:
                generation = generate_warmup_comments(
                    post=post,
                    custom_prompt=payload.customPrompt,
                    comment_count=payload.commentsPerPost,
                )
                if stop_event.is_set():
                    return
                for comment_index, text in enumerate(generation.comments, start=1):
                    result = WarmupCommentResult(
                        postIndex=post.postIndex,
                        postUrl=post.postUrl,
                        commentIndex=comment_index,
                        text=text,
                    )
                    event_queue.put(
                        {
                            "type": "comment_generated",
                            "postIndex": post.postIndex,
                            "postUrl": post.postUrl,
                            "commentIndex": comment_index,
                            "result": result.model_dump(),
                        }
                    )
                event_queue.put(
                    {
                        "type": "post_completed",
                        "postIndex": post.postIndex,
                        "postUrl": post.postUrl,
                        "attachedImageCount": generation.attached_image_count,
                        "message": (
                            f"已生成 {len(generation.comments)} 条评论"
                            f" · 模型已接收 {generation.attached_image_count} 张图片"
                        ),
                    }
                )
            except Exception as exc:
                event_queue.put(
                    {
                        "type": "post_failed",
                        "postIndex": post.postIndex,
                        "postUrl": post.postUrl,
                        "message": f"评论生成失败: {exc}",
                    }
                )
            finally:
                work_queue.task_done()
    finally:
        event_queue.put(None)


def _to_collected_post(post_index: int, result: dict[str, Any]) -> WarmupCollectedPost:
    detail = result.get("detail") or {}
    return WarmupCollectedPost(
        postIndex=post_index,
        postUrl=str(detail.get("post_url") or result.get("postUrl") or ""),
        finalUrl=str(detail.get("final_url") or "") or None,
        title=str(detail.get("title") or result.get("title") or f"Reddit Post #{post_index}"),
        subreddit=str(detail.get("subreddit") or result.get("subreddit") or "") or None,
        author=str(detail.get("author") or "") or None,
        flair=str(detail.get("flair") or "") or None,
        postType=str(detail.get("post_type") or "unknown"),
        bodyText=str(detail.get("body_text") or ""),
        bodyLength=int(detail.get("body_length") or 0),
        mediaUrls=[str(url) for url in detail.get("media_urls") or [] if str(url).strip()],
        outboundUrl=str(detail.get("outbound_url") or "") or None,
        upvotes=detail.get("upvotes"),
        totalCommentCount=detail.get("comments"),
        loadedCommentCount=int(detail.get("loaded_comment_count") or 0),
        includedCommentCount=int(detail.get("included_comment_count") or 0),
        commentTree=detail.get("comment_tree") or {},
    )


def _resolve_post_index(post_indexes: dict[str, int], post_url: str) -> int:
    normalized_url = normalize_post_url(post_url)
    if normalized_url not in post_indexes:
        raise RuntimeError(f"无法匹配帖子任务索引: {post_url}")
    return post_indexes[normalized_url]


def _load_generation_concurrency() -> int:
    raw_value = os.getenv("WARMUP_GENERATION_CONCURRENCY", "").strip()
    if not raw_value:
        return DEFAULT_GENERATION_CONCURRENCY
    try:
        return max(1, min(8, int(raw_value)))
    except ValueError:
        return DEFAULT_GENERATION_CONCURRENCY


def encode_ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
