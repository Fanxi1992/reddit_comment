import type {
  ProductContext,
  CrawlOnlyRequestPayload,
  CrawlOnlyStreamEvent,
  QueryPlanGenerateResponse,
  PostInput,
  CommentDecisionRequestPayload,
  CommentDecisionStreamEvent,
  RedditSearchRequestPayload,
  RedditSearchStreamEvent,
  StreamEvent,
  WarmupCollectRequestPayload,
  WarmupCollectStreamEvent,
  WarmupCommentRequestPayload,
  WarmupCommentStreamEvent,
} from '../types'
import { toPostPayload } from './validation'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

type StreamAnalysisParams = {
  posts: PostInput[]
  customPrompt: string
  signal?: AbortSignal
  onEvent: (event: StreamEvent) => void
}

export async function streamAnalysis({
  posts,
  customPrompt,
  signal,
  onEvent,
}: StreamAnalysisParams): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/analyze/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/x-ndjson',
    },
    body: JSON.stringify({
      posts: toPostPayload(posts),
      customPrompt,
    }),
    signal,
  })

  if (!response.ok) {
    const errorText = await readErrorMessage(response)
    throw new Error(errorText || `请求失败：${response.status}`)
  }

  if (!response.body) {
    throw new Error('浏览器不支持流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) {
      break
    }

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const trimmedLine = line.trim()
      if (!trimmedLine) {
        continue
      }
      onEvent(JSON.parse(trimmedLine) as StreamEvent)
    }
  }

  buffer += decoder.decode()
  if (buffer.trim()) {
    onEvent(JSON.parse(buffer.trim()) as StreamEvent)
  }
}

export async function generateQueryPlan(payload: ProductContext): Promise<QueryPlanGenerateResponse> {
  const response = await fetch(`${API_BASE_URL}/api/query-plan/generate`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const errorText = await readErrorMessage(response)
    throw new Error(errorText || `请求失败：${response.status}`)
  }

  return (await response.json()) as QueryPlanGenerateResponse
}

export async function streamRedditSearch({
  payload,
  signal,
  onEvent,
}: {
  payload: RedditSearchRequestPayload
  signal?: AbortSignal
  onEvent: (event: RedditSearchStreamEvent) => void
}): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/reddit-search/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/x-ndjson',
    },
    body: JSON.stringify({
      ...payload,
      perQueryLimit: payload.perQueryLimit ?? 20,
      searchSort: payload.searchSort ?? 'relevance',
    }),
    signal,
  })

  if (!response.ok) {
    const errorText = await readErrorMessage(response)
    throw new Error(errorText || `请求失败：${response.status}`)
  }

  if (!response.body) {
    throw new Error('浏览器不支持流式响应')
  }

  await readNdjsonStream(response, onEvent)
}

export async function streamCommentDecisions({
  payload,
  signal,
  onEvent,
}: {
  payload: CommentDecisionRequestPayload
  signal?: AbortSignal
  onEvent: (event: CommentDecisionStreamEvent) => void
}): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/comment-decisions/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/x-ndjson',
    },
    body: JSON.stringify(payload),
    signal,
  })

  if (!response.ok) {
    const errorText = await readErrorMessage(response)
    throw new Error(errorText || `请求失败：${response.status}`)
  }

  await readNdjsonStream(response, onEvent)
}

export async function streamCrawlOnly({
  payload,
  signal,
  onEvent,
}: {
  payload: CrawlOnlyRequestPayload
  signal?: AbortSignal
  onEvent: (event: CrawlOnlyStreamEvent) => void
}): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/crawl-only/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/x-ndjson',
    },
    body: JSON.stringify({
      ...payload,
      maxCommentsPerPost: payload.maxCommentsPerPost ?? 30,
      perQueryLimit: payload.perQueryLimit ?? 20,
    }),
    signal,
  })

  if (!response.ok) {
    const errorText = await readErrorMessage(response)
    throw new Error(errorText || `请求失败：${response.status}`)
  }

  await readNdjsonStream(response, onEvent)
}

export async function streamWarmupCollection({
  payload,
  signal,
  onEvent,
}: {
  payload: WarmupCollectRequestPayload
  signal?: AbortSignal
  onEvent: (event: WarmupCollectStreamEvent) => void
}): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/warmup/collect/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/x-ndjson',
    },
    body: JSON.stringify(payload),
    signal,
  })

  if (!response.ok) {
    const errorText = await readErrorMessage(response)
    throw new Error(errorText || `请求失败：${response.status}`)
  }

  await readNdjsonStream(response, onEvent)
}

export async function streamWarmupComments({
  payload,
  signal,
  onEvent,
}: {
  payload: WarmupCommentRequestPayload
  signal?: AbortSignal
  onEvent: (event: WarmupCommentStreamEvent) => void
}): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/warmup/comments/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/x-ndjson',
    },
    body: JSON.stringify(payload),
    signal,
  })

  if (!response.ok) {
    const errorText = await readErrorMessage(response)
    throw new Error(errorText || `请求失败：${response.status}`)
  }

  await readNdjsonStream(response, onEvent)
}

export async function downloadCrawlOnlyArtifact(artifactId: string, format: 'markdown' | 'json'): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/crawl-only/artifacts/${artifactId}/${format}`)

  if (!response.ok) {
    const errorText = await readErrorMessage(response)
    throw new Error(errorText || `下载失败：${response.status}`)
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = readDownloadFileName(response, `reddit-crawl-${artifactId}.${format === 'json' ? 'json' : 'md'}`)
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function readNdjsonStream<T>(response: Response, onEvent: (event: T) => void): Promise<void> {
  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('浏览器不支持流式响应')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) {
      break
    }

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const trimmedLine = line.trim()
      if (!trimmedLine) {
        continue
      }
      onEvent(JSON.parse(trimmedLine) as T)
    }
  }

  buffer += decoder.decode()
  if (buffer.trim()) {
    onEvent(JSON.parse(buffer.trim()) as T)
  }
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.clone().json()
    const detail = payload?.detail

    if (typeof detail === 'string') {
      return detail
    }

    if (Array.isArray(detail)) {
      return detail
        .map((item) => item?.msg || item?.message || JSON.stringify(item))
        .filter(Boolean)
        .join('；')
    }
  } catch {
    // Fall back to plain text below.
  }

  return response.text()
}

function readDownloadFileName(response: Response, fallback: string): string {
  const disposition = response.headers.get('content-disposition')
  if (!disposition) {
    return fallback
  }

  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1])
  }

  const simpleMatch = disposition.match(/filename="?([^"]+)"?/i)
  return simpleMatch?.[1] ?? fallback
}
