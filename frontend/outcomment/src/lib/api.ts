import type {
  ProductContext,
  QueryPlanGenerateResponse,
  PostInput,
  CommentDecisionRequestPayload,
  CommentDecisionStreamEvent,
  RedditSearchRequestPayload,
  RedditSearchStreamEvent,
  StreamEvent,
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
