export type PostSource = 'manual' | 'excel'
export type ValidationStatus = 'valid' | 'invalid' | 'duplicate'
export type AnalysisStatus = 'queued' | 'processing' | 'success' | 'skipped' | 'failed'
export type TaskStage = 'idle' | 'crawling' | 'analyzing' | 'completed' | 'failed' | 'cancelled'

export type PostInput = {
  id: string
  url: string
  source: PostSource
  validationStatus: ValidationStatus
  validationMessage?: string
}

export type PostPayload = {
  url: string
}

export type AnalysisResult = {
  title: string
  url?: string | null
  inputUrl?: string | null
  communityName?: string | null
  parsedCommunityName?: string | null
  status: 'success' | 'skipped' | 'failed'
  reason?: string | null
  textPreview?: string | null
  imageCount: number
  analysis?: string | null
}

export type ResultItem = {
  id: string
  input: PostInput
  status: AnalysisStatus
  backendTitle?: string
  communityName?: string | null
  parsedCommunityName?: string | null
  reason?: string | null
  textPreview?: string | null
  imageCount?: number
  analysis?: string | null
}

export type StreamSummary = {
  total: number
  processed: number
  skipped: number
  failed: number
}

export type StreamEvent =
  | {
      type: 'crawl_started'
      message?: string
    }
  | {
      type: 'crawl_completed'
      total?: number
      message?: string
    }
  | {
      type: 'post_started'
      message?: string
      inputUrl?: string
    }
  | {
      type: 'post_result'
      result: AnalysisResult
    }
  | {
      type: 'done'
      message?: string
      summary?: StreamSummary
    }
  | {
      type: 'error'
      message?: string
    }

export type ImportReport = {
  totalRows: number
  validRows: number
  duplicateRows: number
  invalidRows: number
}

export type QueryIntent =
  | 'pain_point'
  | 'recommendation'
  | 'review'
  | 'alternative'
  | 'comparison'
  | 'problem_solution'
  | 'community_discussion'
  | 'other'

export type SuggestedTimeRange = 'week' | 'month' | 'all'

export type ProductContext = {
  productName: string
  productDescription: string
  targetAudience: string
  sellingPoints: string
  competitors: string
  commentRequirements: string
  forbiddenTopics: string
  desiredQueryCount: number
}

export type PlannedQuery = {
  id: string
  query: string
  intent: QueryIntent
  reason: string
  priority: number
  suggestedTimeRange: SuggestedTimeRange
  targetUrlCount?: number | null
}

export type PlannedQueryPayload = Omit<PlannedQuery, 'id'>

export type QueryPlanGenerateResponse = {
  queries: PlannedQueryPayload[]
}

export type ApprovedQueryPlan = {
  productContext: ProductContext
  queries: PlannedQuery[]
  approvedAt: string
}

export type RedditSearchStatus = 'pending' | 'running' | 'success' | 'no_results' | 'failed'

export type RedditSearchResultItem = {
  query: string
  queryIntent: QueryIntent
  priority: number
  timeRange: SuggestedTimeRange
  resultIndex: number
  postUrl: string
  postId: string
  title: string
  subreddit: string
  ageText: string
  votes?: number | null
  comments?: number | null
  duplicateOfQuery?: string | null
  matchedQueries: string[]
}

export type RedditSearchSummary = {
  totalQueries: number
  successfulQueries: number
  failedQueries: number
  rawUrlCount: number
  uniqueUrlCount: number
}

export type RedditSearchRequestPayload = {
  productContext: ProductContext
  queries: PlannedQuery[]
  perQueryLimit?: number
  searchSort?: 'relevance'
}

export type RedditSearchStreamEvent =
  | {
      type: 'search_started'
      totalQueries: number
      perQueryLimit: number
      totalTargetUrlCount?: number
      searchSort: 'relevance'
    }
  | {
      type: 'query_started'
      queryIndex: number
      query: string
      timeRange: SuggestedTimeRange
      targetUrlCount?: number
    }
  | {
      type: 'query_result'
      queryIndex: number
      query: string
      targetUrlCount?: number
      status: Exclude<RedditSearchStatus, 'pending' | 'running'>
      reason: string
      searchResultsUrl: string
      rawResultCount: number
      uniqueResultCount: number
      results: RedditSearchResultItem[]
    }
  | {
      type: 'summary'
      summary: RedditSearchSummary
      results: RedditSearchResultItem[]
    }
  | {
      type: 'done'
      summary: RedditSearchSummary
      results: RedditSearchResultItem[]
    }
  | {
      type: 'error'
      message: string
    }

export type QuerySearchState = {
  status: RedditSearchStatus
  reason?: string
  rawResultCount: number
  uniqueResultCount: number
}

export type CommentDecisionStatus = 'pending' | 'detail' | 'gemini' | 'success' | 'skipped' | 'failed'
export type DecisionEnvironmentStatus = 'starting' | 'running' | 'completed' | 'failed'

export type CommentDecisionRequestPayload = {
  productContext: ProductContext
  queries: PlannedQuery[]
  searchResults: RedditSearchResultItem[]
  maxSuggestions?: number
  commentLengthDistribution?: CommentLengthDistribution
}

export type CommentLengthStyle = 'short' | 'medium' | 'long'

export type CommentLengthDistribution = {
  short: number
  medium: number
  long: number
}

export type CommentDecisionResult = {
  postUrl: string
  sourceQuery: string
  subreddit?: string | null
  title?: string | null
  status: 'success' | 'skipped' | 'failed'
  reason?: string | null
  commentUrl?: string | null
  commentText?: string | null
  environmentId?: string | null
  commentLengthStyle?: CommentLengthStyle | null
}

export type CommentDecisionSummary = {
  totalPosts: number
  processedPosts: number
  successCount: number
  skippedCount: number
  failedCount: number
}

export type DecisionPostState = {
  status: CommentDecisionStatus
  title: string
  subreddit: string
  sourceQuery: string
  reason?: string | null
  environmentId?: string | null
  commentUrl?: string | null
  commentText?: string | null
  commentLengthStyle?: CommentLengthStyle | null
}

export type DecisionEnvironmentState = {
  status: DecisionEnvironmentStatus
  environmentIndex: number
  userId: string
  totalPosts: number
  processed: number
  success: number
  skipped: number
  failed: number
}

export type CommentDecisionStreamEvent =
  | {
      type: 'decision_started'
      totalPosts: number
      environmentCount: number
      maxSuggestions?: number | null
    }
  | {
      type: 'environment_started'
      environmentId: string
      environmentIndex: number
      userId: string
      totalPosts: number
    }
  | {
      type: 'post_started'
      environmentId: string
      postUrl: string
      title: string
    }
  | {
      type: 'detail_collected'
      environmentId: string
      postUrl: string
      title: string
      subreddit: string
      commentCount: number
      mediaCount: number
    }
  | {
      type: 'gemini_started'
      environmentId: string
      postUrl: string
    }
  | {
      type: 'post_result'
      environmentId: string
      result: CommentDecisionResult
    }
  | {
      type: 'environment_finished'
      environmentId: string
      environmentIndex: number
      userId: string
      totalPosts: number
      processed: number
      success: number
      skipped: number
      failed: number
    }
  | {
      type: 'done'
      summary: CommentDecisionSummary
      results: CommentDecisionResult[]
    }
  | {
      type: 'error'
      message: string
    }

export type CrawlOnlySource = 'simulated_search' | 'manual_urls'

export type CrawlOnlyRequestPayload = {
  source: CrawlOnlySource
  queries?: PlannedQuery[]
  urls?: string[]
  maxCommentsPerPost?: number
  perQueryLimit?: number
}

export type CrawlOnlyResult = {
  postUrl: string
  title?: string | null
  subreddit?: string | null
  status: 'success' | 'skipped' | 'failed'
  reason?: string | null
  environmentId?: string | null
  searchResult?: RedditSearchResultItem
  detail?: Record<string, unknown> | null
}

export type CrawlOnlySummary = {
  totalPosts: number
  processedPosts: number
  successCount: number
  skippedCount: number
  failedCount: number
}

export type CrawlOnlyArtifact = {
  artifactId: string
  jsonPath?: string
  markdownPath?: string
}

export type CrawlOnlyStreamEvent =
  | {
      type: 'crawl_started'
      source: CrawlOnlySource
      maxCommentsPerPost: number
      perQueryLimit: number
    }
  | {
      type: 'search_started'
      totalQueries: number
      perQueryLimit: number
      totalTargetUrlCount?: number
      searchSort: 'relevance'
    }
  | {
      type: 'query_started'
      queryIndex: number
      query: string
      timeRange: SuggestedTimeRange
      targetUrlCount?: number
      environmentId?: string
      environmentIndex?: number
    }
  | {
      type: 'query_result'
      queryIndex: number
      query: string
      targetUrlCount?: number
      status: Exclude<RedditSearchStatus, 'pending' | 'running'>
      reason: string
      searchResultsUrl: string
      rawResultCount: number
      uniqueResultCount: number
      results: RedditSearchResultItem[]
    }
  | {
      type: 'search_completed'
      summary: RedditSearchSummary
      results: RedditSearchResultItem[]
    }
  | {
      type: 'environment_started'
      environmentId: string
      environmentIndex: number
      userId: string
      totalPosts: number
    }
  | {
      type: 'environment_finished'
      environmentId: string
      environmentIndex: number
      userId: string
      totalPosts: number
    }
  | {
      type: 'post_started'
      environmentId: string
      postUrl: string
      title: string
    }
  | {
      type: 'post_result'
      environmentId: string
      result: CrawlOnlyResult
    }
  | ({
      type: 'artifact_ready'
    } & CrawlOnlyArtifact)
  | ({
      type: 'done'
      summary: CrawlOnlySummary
      results: CrawlOnlyResult[]
    } & CrawlOnlyArtifact)
  | {
      type: 'error'
      message: string
    }
