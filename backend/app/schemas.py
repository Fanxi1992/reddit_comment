from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class PostInput(BaseModel):
    url: HttpUrl


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
    communityName: str | None = None
    parsedCommunityName: str | None = None
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
    inputUrl: str | None = None
    total: int | None = None
    result: PostAnalysisResult | None = None
    summary: dict[str, int] | None = None


QueryIntent = Literal[
    "pain_point",
    "recommendation",
    "review",
    "alternative",
    "comparison",
    "problem_solution",
    "community_discussion",
    "other",
]

SearchTimeRange = Literal["week", "month", "all"]
SearchSort = Literal["relevance"]


class QueryPlanGenerateRequest(BaseModel):
    productName: str = Field(..., min_length=1, max_length=200)
    productDescription: str = Field(..., min_length=1, max_length=4000)
    targetAudience: str = Field(default="", max_length=2000)
    sellingPoints: str = Field(default="", max_length=2000)
    competitors: str = Field(default="", max_length=2000)
    commentRequirements: str = Field(default="", max_length=3000)
    forbiddenTopics: str = Field(default="", max_length=2000)
    desiredQueryCount: int = Field(default=20, ge=5, le=50)


class PlannedQuery(BaseModel):
    query: str = Field(..., min_length=2, max_length=120)
    intent: QueryIntent
    reason: str = Field(..., min_length=1, max_length=500)
    priority: int = Field(..., ge=1, le=5)
    suggestedTimeRange: SearchTimeRange = "week"


class QueryPlanGenerateResponse(BaseModel):
    queries: list[PlannedQuery]


class RedditSearchRequest(BaseModel):
    productContext: QueryPlanGenerateRequest
    queries: list[PlannedQuery] = Field(..., min_length=1, max_length=50)
    perQueryLimit: int = Field(default=20, ge=1, le=50)
    searchSort: SearchSort = "relevance"


class RedditSearchResultItem(BaseModel):
    query: str
    queryIntent: QueryIntent
    priority: int
    timeRange: SearchTimeRange
    resultIndex: int
    postUrl: str
    postId: str
    title: str
    subreddit: str
    ageText: str
    votes: int | None = None
    comments: int | None = None
    duplicateOfQuery: str | None = None
    matchedQueries: list[str] = Field(default_factory=list)


class RedditSearchSummary(BaseModel):
    totalQueries: int
    successfulQueries: int
    failedQueries: int
    rawUrlCount: int
    uniqueUrlCount: int
