import asyncio
import queue
import threading
from collections.abc import Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.comment_decision_runner import encode_ndjson as encode_comment_ndjson
from app.comment_decision_runner import run_comment_decision_stream
from app.crawl_only_runner import encode_ndjson as encode_crawl_only_ndjson
from app.crawl_only_runner import resolve_crawl_only_artifact_path, run_crawl_only_stream
from app.query_planner import generate_query_plan
from app.reddit_analyzer import MAX_POSTS_PER_BATCH, stream_reddit_analysis
from app.reddit_searcher import encode_ndjson, run_reddit_search_batch
from app.schemas import (
    AnalyzeRequest,
    CommentDecisionRequest,
    CrawlOnlyRequest,
    QueryPlanGenerateRequest,
    QueryPlanGenerateResponse,
    RedditSearchRequest,
)


MAX_CONCURRENT_ANALYSES = 3
MAX_CONCURRENT_REDDIT_SEARCHES = 1
MAX_CONCURRENT_COMMENT_DECISIONS = 1
MAX_CONCURRENT_CRAWL_ONLY_TASKS = 1


class AnalysisTaskLimiter:
    def __init__(self, max_running: int) -> None:
        self.max_running = max_running
        self._running = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._running >= self.max_running:
                return False
            self._running += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._running = max(0, self._running - 1)


task_limiter = AnalysisTaskLimiter(MAX_CONCURRENT_ANALYSES)
reddit_search_limiter = AnalysisTaskLimiter(MAX_CONCURRENT_REDDIT_SEARCHES)
comment_decision_limiter = AnalysisTaskLimiter(MAX_CONCURRENT_COMMENT_DECISIONS)
crawl_only_limiter = AnalysisTaskLimiter(MAX_CONCURRENT_CRAWL_ONLY_TASKS)


app = FastAPI(title="Reddit Insight Analyzer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/query-plan/generate", response_model=QueryPlanGenerateResponse)
def generate_reddit_query_plan(payload: QueryPlanGenerateRequest) -> QueryPlanGenerateResponse:
    try:
        return generate_query_plan(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Query 规划生成失败: {exc}") from exc


@app.post("/api/reddit-search/stream")
async def stream_reddit_search(payload: RedditSearchRequest, request: Request) -> StreamingResponse:
    if not reddit_search_limiter.try_acquire():
        raise HTTPException(status_code=429, detail="当前已有 Reddit 搜索任务正在运行，ADSpower环境数量有限，请稍等十分钟再试")

    async def stream_with_release():
        stop_event = threading.Event()
        producer_thread = None
        try:
            yield encode_ndjson(
                {
                    "type": "search_started",
                    "totalQueries": len(payload.queries),
                    "perQueryLimit": payload.perQueryLimit,
                    "searchSort": payload.searchSort,
                }
            )
            event_queue: queue.Queue[dict | None] = queue.Queue()
            producer_thread = threading.Thread(
                target=_produce_stream_events,
                args=(run_reddit_search_batch(payload, stop_event=stop_event), event_queue),
                daemon=True,
            )
            producer_thread.start()
            while True:
                if await request.is_disconnected():
                    stop_event.set()
                    return

                event = await asyncio.to_thread(_get_stream_event, event_queue, 0.5)
                if event is _QUEUE_TIMEOUT:
                    continue
                if event is None:
                    return
                yield encode_ndjson(event)
        except Exception as exc:
            yield encode_ndjson({"type": "error", "message": f"Reddit 搜索失败: {exc}"})
        finally:
            stop_event.set()
            if producer_thread is not None and producer_thread.is_alive():
                await _join_thread_until_done(producer_thread)
            reddit_search_limiter.release()

    return StreamingResponse(
        stream_with_release(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/comment-decisions/stream")
async def stream_comment_decisions(payload: CommentDecisionRequest, request: Request) -> StreamingResponse:
    if not comment_decision_limiter.try_acquire():
        raise HTTPException(status_code=429, detail="当前已有评论决策任务正在运行，ADSpower环境数量有限，请稍等十分钟再试")

    async def stream_with_release():
        stop_event = threading.Event()
        producer_thread = None
        try:
            event_queue: queue.Queue[dict | None] = queue.Queue()
            producer_thread = threading.Thread(
                target=_produce_stream_events,
                args=(run_comment_decision_stream(payload, stop_event=stop_event), event_queue),
                daemon=True,
            )
            producer_thread.start()
            while True:
                if await request.is_disconnected():
                    stop_event.set()
                    return

                event = await asyncio.to_thread(_get_stream_event, event_queue, 0.5)
                if event is _QUEUE_TIMEOUT:
                    continue
                if event is None:
                    return
                yield encode_comment_ndjson(event)
        except Exception as exc:
            yield encode_comment_ndjson({"type": "error", "message": f"评论决策失败: {exc}"})
        finally:
            stop_event.set()
            if producer_thread is not None and producer_thread.is_alive():
                await _join_thread_until_done(producer_thread)
            comment_decision_limiter.release()

    return StreamingResponse(
        stream_with_release(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/crawl-only/stream")
async def stream_crawl_only(payload: CrawlOnlyRequest, request: Request) -> StreamingResponse:
    if not crawl_only_limiter.try_acquire():
        raise HTTPException(status_code=429, detail="当前已有仅抓取任务正在运行，ADSpower环境数量有限，请稍等十分钟再试")

    async def stream_with_release():
        stop_event = threading.Event()
        producer_thread = None
        try:
            event_queue: queue.Queue[dict | None] = queue.Queue()
            producer_thread = threading.Thread(
                target=_produce_stream_events,
                args=(run_crawl_only_stream(payload, stop_event=stop_event), event_queue),
                daemon=True,
            )
            producer_thread.start()
            while True:
                if await request.is_disconnected():
                    stop_event.set()
                    return

                event = await asyncio.to_thread(_get_stream_event, event_queue, 0.5)
                if event is _QUEUE_TIMEOUT:
                    continue
                if event is None:
                    return
                yield encode_crawl_only_ndjson(event)
        except Exception as exc:
            yield encode_crawl_only_ndjson({"type": "error", "message": f"仅抓取任务失败: {exc}"})
        finally:
            stop_event.set()
            if producer_thread is not None and producer_thread.is_alive():
                await _join_thread_until_done(producer_thread)
            crawl_only_limiter.release()

    return StreamingResponse(
        stream_with_release(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/crawl-only/artifacts/{artifact_id}/markdown")
def download_crawl_only_markdown(artifact_id: str) -> FileResponse:
    try:
        path = resolve_crawl_only_artifact_path(artifact_id, "markdown")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact_not_found") from exc
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)


@app.get("/api/crawl-only/artifacts/{artifact_id}/json")
def download_crawl_only_json(artifact_id: str) -> FileResponse:
    try:
        path = resolve_crawl_only_artifact_path(artifact_id, "json")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact_not_found") from exc
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.post("/api/analyze/stream")
async def analyze_reddit_posts(payload: AnalyzeRequest, request: Request) -> StreamingResponse:
    posts = _dedupe_posts(
        [
            {
                "url": str(post.url),
            }
            for post in payload.posts
        ]
        if payload.posts
        else [{"url": str(url)} for url in payload.startUrls or []]
    )

    if len(posts) > MAX_POSTS_PER_BATCH:
        raise HTTPException(
            status_code=422,
            detail=f"单批最多支持 {MAX_POSTS_PER_BATCH} 条有效 Reddit 帖子链接，请减少或去重后再提交",
        )

    if not task_limiter.try_acquire():
        raise HTTPException(
            status_code=429,
            detail=f"当前已有 {MAX_CONCURRENT_ANALYSES} 个分析任务正在运行，ADSpower环境数量有限，请稍等十分钟再试",
        )

    async def should_stop() -> bool:
        return await request.is_disconnected()

    async def stream_with_release():
        try:
            async for chunk in stream_reddit_analysis(
                posts=posts,
                custom_prompt=payload.customPrompt,
                max_items=payload.maxItems,
                should_stop=should_stop,
            ):
                yield chunk
        finally:
            task_limiter.release()

    return StreamingResponse(
        stream_with_release(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _dedupe_posts(posts: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []

    for post in posts:
        url = post.get("url", "").strip()
        if not url:
            continue

        normalized_url = _normalize_url(url)
        if normalized_url in seen:
            continue

        seen.add(normalized_url)
        deduped.append({"url": url})

    return deduped


def _normalize_url(url: str) -> str:
    normalized = url.strip()
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized.lower()


_QUEUE_TIMEOUT = object()


async def _join_thread_until_done(thread: threading.Thread) -> None:
    while thread.is_alive():
        try:
            await asyncio.to_thread(thread.join, 0.5)
        except asyncio.CancelledError:
            continue


def _get_stream_event(event_queue: queue.Queue[dict | None], timeout: float) -> dict | None | object:
    try:
        return event_queue.get(timeout=timeout)
    except queue.Empty:
        return _QUEUE_TIMEOUT


def _produce_stream_events(iterator: Iterator[dict], event_queue: queue.Queue[dict | None]) -> None:
    try:
        for event in iterator:
            event_queue.put(event)
    except Exception as exc:
        event_queue.put({"type": "error", "message": str(exc)})
    finally:
        if hasattr(iterator, "close"):
            try:
                iterator.close()
            except Exception:
                pass
        event_queue.put(None)
