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
      searchSort: 'relevance'
    }
  | {
      type: 'query_started'
      queryIndex: number
      query: string
      timeRange: SuggestedTimeRange
    }
  | {
      type: 'query_result'
      queryIndex: number
      query: string
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
