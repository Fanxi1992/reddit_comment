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
MAX_SEARCH_URL_BUDGET = 120


class SearchFilterCriteria(BaseModel):
    maxAgeDays: int | None = Field(default=None, ge=1, le=3650)
    minVotes: int | None = Field(default=None, ge=0, le=10_000_000)
    minComments: int | None = Field(default=None, ge=0, le=10_000_000)


class QueryPlanGenerateRequest(BaseModel):
    productName: str = Field(..., min_length=1, max_length=200)
    productDescription: str = Field(..., min_length=1, max_length=4000)
    targetAudience: str = Field(default="", max_length=2000)
    sellingPoints: str = Field(default="", max_length=2000)
    competitors: str = Field(default="", max_length=2000)
    commentRequirements: str = Field(default="", max_length=3000)
    forbiddenTopics: str = Field(default="", max_length=2000)
    desiredQueryCount: int = Field(default=12, ge=1, le=20)


class PlannedQuery(BaseModel):
    query: str = Field(..., min_length=2, max_length=120)
    intent: QueryIntent
    reason: str = Field(..., min_length=1, max_length=500)
    priority: int = Field(..., ge=1, le=5)
    suggestedTimeRange: SearchTimeRange = "week"
    targetUrlCount: int | None = Field(default=None, ge=1, le=MAX_SEARCH_URL_BUDGET)


class QueryPlanGenerateResponse(BaseModel):
    queries: list[PlannedQuery]


class RedditSearchRequest(BaseModel):
    productContext: QueryPlanGenerateRequest
    queries: list[PlannedQuery] = Field(..., min_length=1, max_length=6)
    perQueryLimit: int = Field(default=20, ge=1, le=MAX_SEARCH_URL_BUDGET)
    searchSort: SearchSort = "relevance"
    searchFilter: SearchFilterCriteria | None = None

    @model_validator(mode="after")
    def validate_query_url_budget(self) -> "RedditSearchRequest":
        _validate_query_url_budget(self.queries, self.perQueryLimit)
        return self


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


CommentDecisionStatus = Literal["success", "skipped", "failed"]
CommentLengthStyle = Literal["short", "medium", "long"]


class CommentLengthDistribution(BaseModel):
    short: int = Field(default=30, ge=0, le=100)
    medium: int = Field(default=50, ge=0, le=100)
    long: int = Field(default=20, ge=0, le=100)

    @model_validator(mode="after")
    def validate_total(self) -> "CommentLengthDistribution":
        if self.short + self.medium + self.long != 100:
            raise ValueError("评论长度比例总和必须等于 100")
        return self


class CommentDecisionRequest(BaseModel):
    productContext: QueryPlanGenerateRequest
    queries: list[PlannedQuery] = Field(..., min_length=1, max_length=6)
    searchResults: list[RedditSearchResultItem] = Field(..., min_length=1, max_length=120)
    maxSuggestions: int | None = Field(default=None, ge=1, le=200)
    commentLengthDistribution: CommentLengthDistribution = Field(default_factory=CommentLengthDistribution)


class CommentDecisionResult(BaseModel):
    postUrl: str
    sourceQuery: str
    subreddit: str | None = None
    title: str | None = None
    status: CommentDecisionStatus
    reason: str | None = None
    commentUrl: str | None = None
    commentText: str | None = None
    environmentId: str | None = None
    commentLengthStyle: CommentLengthStyle | None = None


class CommentDecisionSummary(BaseModel):
    totalPosts: int
    processedPosts: int
    successCount: int
    skippedCount: int
    failedCount: int


CrawlOnlySource = Literal["simulated_search", "manual_urls"]
CrawlOnlyStatus = Literal["success", "skipped", "failed"]


class CrawlOnlyRequest(BaseModel):
    source: CrawlOnlySource
    queries: list[PlannedQuery] | None = Field(default=None, max_length=6)
    urls: list[HttpUrl] | None = Field(default=None, max_length=120)
    maxCommentsPerPost: int = Field(default=30, ge=1, le=200)
    perQueryLimit: int = Field(default=20, ge=1, le=MAX_SEARCH_URL_BUDGET)
    searchFilter: SearchFilterCriteria | None = None

    @model_validator(mode="after")
    def validate_source_inputs(self) -> "CrawlOnlyRequest":
        if self.source == "simulated_search" and not self.queries:
            raise ValueError("模拟搜索（仅抓取）至少需要 1 条 query")
        if self.source == "simulated_search":
            _validate_query_url_budget(self.queries or [], self.perQueryLimit)
        if self.source == "manual_urls" and not self.urls:
            raise ValueError("手动导入 URL（仅抓取）至少需要 1 条 Reddit URL")
        return self


class CrawlOnlyResult(BaseModel):
    postUrl: str
    title: str | None = None
    subreddit: str | None = None
    status: CrawlOnlyStatus
    reason: str | None = None
    environmentId: str | None = None
    detail: dict | None = None


class CrawlOnlySummary(BaseModel):
    totalPosts: int
    processedPosts: int
    successCount: int
    skippedCount: int
    failedCount: int


def _validate_query_url_budget(queries: list[PlannedQuery], fallback_limit: int) -> None:
    total_budget = sum(query.targetUrlCount or fallback_limit for query in queries)
    if total_budget > MAX_SEARCH_URL_BUDGET:
        raise ValueError(f"Query URL 抓取数量总和不能超过 {MAX_SEARCH_URL_BUDGET}")
