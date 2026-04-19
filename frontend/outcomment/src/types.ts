export type PostSource = 'manual' | 'excel'
export type ValidationStatus = 'valid' | 'invalid' | 'duplicate'
export type AnalysisStatus = 'queued' | 'processing' | 'success' | 'skipped' | 'failed'
export type TaskStage = 'idle' | 'crawling' | 'analyzing' | 'completed' | 'failed'

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
