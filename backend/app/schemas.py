from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class PostInput(BaseModel):
    url: HttpUrl
    title: str | None = None
    community: str | None = None


class AnalyzeRequest(BaseModel):
    posts: list[PostInput] | None = None
    startUrls: list[HttpUrl] | None = None
    customPrompt: str = Field(..., min_length=1)
    maxItems: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_posts_or_start_urls(self) -> "AnalyzeRequest":
        if not self.posts and not self.startUrls:
            raise ValueError("posts 或 startUrls 至少需要提供一项")
        return self


class PostAnalysisResult(BaseModel):
    title: str
    url: str | None = None
    inputUrl: str | None = None
    inputTitle: str | None = None
    inputCommunity: str | None = None
    status: Literal["success", "skipped", "failed"]
    reason: str | None = None
    textPreview: str | None = None
    imageCount: int = 0
    analysis: str | None = None


class StreamEvent(BaseModel):
    type: Literal[
        "crawl_started",
        "crawl_completed",
        "post_started",
        "post_result",
        "done",
        "error",
    ]
    message: str | None = None
    total: int | None = None
    result: PostAnalysisResult | None = None
    summary: dict[str, int] | None = None
