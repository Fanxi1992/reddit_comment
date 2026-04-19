from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.reddit_analyzer import stream_reddit_analysis
from app.schemas import AnalyzeRequest


app = FastAPI(title="Reddit Insight Analyzer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze/stream")
def analyze_reddit_posts(payload: AnalyzeRequest) -> StreamingResponse:
    posts = (
        [
            {
                "url": str(post.url),
            }
            for post in payload.posts
        ]
        if payload.posts
        else [{"url": str(url)} for url in payload.startUrls or []]
    )

    return StreamingResponse(
        stream_reddit_analysis(
            posts=posts,
            custom_prompt=payload.customPrompt,
            max_items=payload.maxItems,
        ),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
