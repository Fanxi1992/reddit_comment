import threading
import asyncio
from collections.abc import Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.query_planner import generate_query_plan
from app.reddit_analyzer import MAX_POSTS_PER_BATCH, stream_reddit_analysis
from app.reddit_searcher import encode_ndjson, run_reddit_search_batch
from app.schemas import (
    AnalyzeRequest,
    QueryPlanGenerateRequest,
    QueryPlanGenerateResponse,
    RedditSearchRequest,
)


MAX_CONCURRENT_ANALYSES = 3
MAX_CONCURRENT_REDDIT_SEARCHES = 1


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
        raise HTTPException(status_code=429, detail="当前已有 Reddit 搜索任务正在运行，请稍后再试")

    async def stream_with_release():
        try:
            yield encode_ndjson(
                {
                    "type": "search_started",
                    "totalQueries": len(payload.queries),
                    "perQueryLimit": payload.perQueryLimit,
                    "searchSort": payload.searchSort,
                }
            )
            iterator = run_reddit_search_batch(payload)
            while True:
                if await request.is_disconnected():
                    yield encode_ndjson({"type": "error", "message": "客户端已断开连接，搜索任务停止"})
                    return

                event = await asyncio.to_thread(_next_stream_event, iterator)
                if event is None:
                    return
                yield encode_ndjson(event)
        except Exception as exc:
            yield encode_ndjson({"type": "error", "message": f"Reddit 搜索失败: {exc}"})
        finally:
            reddit_search_limiter.release()

    return StreamingResponse(
        stream_with_release(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
            detail=f"当前已有 {MAX_CONCURRENT_ANALYSES} 个分析任务正在运行，请稍后再试",
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


def _next_stream_event(iterator: Iterator[dict]) -> dict | None:
    try:
        return next(iterator)
    except StopIteration:
        return None
