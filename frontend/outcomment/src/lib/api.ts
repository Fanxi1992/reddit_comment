import type { PostInput, StreamEvent } from '../types'
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
    const errorText = await response.text()
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
