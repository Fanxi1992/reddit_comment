import { useMemo, useRef, useState } from 'react'

import { downloadCrawlOnlyArtifact, streamCrawlOnly } from '../lib/api'
import type {
  CrawlOnlyArtifact,
  CrawlOnlyRequestPayload,
  CrawlOnlyResult,
  CrawlOnlySource,
  CrawlOnlyStreamEvent,
  PlannedQuery,
  RedditSearchResultItem,
} from '../types'
import { DownloadIcon, PlayIcon, StopIcon } from './icons'

type CrawlOnlyPanelProps = {
  source: CrawlOnlySource
  queries?: PlannedQuery[]
  searchResults?: RedditSearchResultItem[]
}

type PostState = {
  status: 'pending' | 'running' | 'success' | 'skipped' | 'failed'
  title: string
  subreddit: string
  reason?: string | null
  environmentId?: string | null
}

type EnvironmentState = {
  environmentIndex: number
  userId: string
  totalPosts: number
  status: 'running' | 'completed'
}

const SOURCE_LABELS: Record<CrawlOnlySource, string> = {
  simulated_search: '模拟搜索（仅抓取）',
  manual_urls: '手动导入 URL（仅抓取）',
}

export function CrawlOnlyPanel({ source, queries = [], searchResults = [] }: CrawlOnlyPanelProps) {
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState('等待开始')
  const [maxCommentsPerPost, setMaxCommentsPerPost] = useState('30')
  const [perQueryLimit, setPerQueryLimit] = useState('20')
  const [results, setResults] = useState<CrawlOnlyResult[]>([])
  const [postStates, setPostStates] = useState<Record<string, PostState>>({})
  const [environmentStates, setEnvironmentStates] = useState<Record<string, EnvironmentState>>({})
  const [artifact, setArtifact] = useState<CrawlOnlyArtifact | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const canStart = source === 'simulated_search' ? queries.length > 0 : searchResults.length > 0
  const summary = useMemo(() => summarizeResults(results, postStates), [postStates, results])

  const handleStart = async () => {
    const controller = new AbortController()
    abortRef.current = controller
    setIsRunning(true)
    setError(null)
    setMessage('正在启动仅抓取任务')
    setResults([])
    setArtifact(null)
    setEnvironmentStates({})
    setPostStates(createInitialPostStates(searchResults))

    const payload: CrawlOnlyRequestPayload = {
      source,
      maxCommentsPerPost: clampNumber(maxCommentsPerPost, 1, 200, 30),
      perQueryLimit: clampNumber(perQueryLimit, 1, 50, 20),
    }
    if (source === 'simulated_search') {
      payload.queries = queries
    } else {
      payload.urls = searchResults.map((item) => item.postUrl)
    }

    try {
      await streamCrawlOnly({
        payload,
        signal: controller.signal,
        onEvent: applyEvent,
      })
    } catch (exc) {
      if (controller.signal.aborted) {
        setMessage('任务已停止')
        setPostStates((current) => markRunningPostsSkipped(current))
      } else {
        setError(exc instanceof Error ? exc.message : '仅抓取任务失败')
        setMessage('任务失败')
      }
    } finally {
      setIsRunning(false)
      abortRef.current = null
    }
  }

  const handleStop = () => {
    abortRef.current?.abort()
    setIsRunning(false)
  }

  const applyEvent = (event: CrawlOnlyStreamEvent) => {
    if (event.type === 'crawl_started') {
      setMessage('仅抓取任务已启动')
      return
    }
    if (event.type === 'search_started') {
      setMessage(`正在执行 ${event.totalQueries} 条搜索 Query`)
      return
    }
    if (event.type === 'query_started') {
      setMessage(`正在搜索：${event.query}`)
      return
    }
    if (event.type === 'query_result') {
      setMessage(`搜索完成：${event.query}，去重 ${event.uniqueResultCount} 条 URL`)
      return
    }
    if (event.type === 'search_completed') {
      setMessage(`URL 已锁定：${event.summary.uniqueUrlCount} 条`)
      setPostStates(createInitialPostStates(event.results))
      return
    }
    if (event.type === 'environment_started') {
      setEnvironmentStates((current) => ({
        ...current,
        [event.environmentId]: {
          environmentIndex: event.environmentIndex,
          userId: event.userId,
          totalPosts: event.totalPosts,
          status: 'running',
        },
      }))
      return
    }
    if (event.type === 'environment_finished') {
      setEnvironmentStates((current) => ({
        ...current,
        [event.environmentId]: {
          ...(current[event.environmentId] ?? {
            environmentIndex: event.environmentIndex,
            userId: event.userId,
            totalPosts: event.totalPosts,
          }),
          status: 'completed',
        },
      }))
      return
    }
    if (event.type === 'post_started') {
      setPostStates((current) => ({
        ...current,
        [event.postUrl]: {
          ...(current[event.postUrl] ?? { title: event.title, subreddit: '', status: 'pending' }),
          status: 'running',
          title: event.title,
          environmentId: event.environmentId,
          reason: '详情抓取中',
        },
      }))
      return
    }
    if (event.type === 'post_result') {
      const result = event.result
      setResults((current) => [...current.filter((item) => item.postUrl !== result.postUrl), result])
      setPostStates((current) => ({
        ...current,
        [result.postUrl]: {
          ...(current[result.postUrl] ?? { title: result.title || result.postUrl, subreddit: result.subreddit || '', status: 'pending' }),
          status: result.status,
          title: result.title || result.postUrl,
          subreddit: result.subreddit || '',
          reason: result.reason,
          environmentId: event.environmentId,
        },
      }))
      return
    }
    if (event.type === 'artifact_ready') {
      setArtifact(event)
      setMessage('产物已生成')
      return
    }
    if (event.type === 'done') {
      setResults(event.results)
      setArtifact(event)
      setMessage('仅抓取任务完成')
      return
    }
    if (event.type === 'error') {
      setError(event.message)
      setMessage('任务失败')
    }
  }

  return (
    <section className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-950">语料仅抓取</h2>
          <p className="mt-1 text-sm text-slate-500">{SOURCE_LABELS[source]} · 抓取帖子正文和评论树，输出 MD/JSON。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="h-9 w-28 rounded-md border border-slate-300 px-2 text-sm outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-100"
            disabled={isRunning}
            max={200}
            min={1}
            onChange={(event) => setMaxCommentsPerPost(event.target.value)}
            title="每帖评论上限"
            type="number"
            value={maxCommentsPerPost}
          />
          {source === 'simulated_search' ? (
            <input
              className="h-9 w-28 rounded-md border border-slate-300 px-2 text-sm outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-100"
              disabled={isRunning}
              max={50}
              min={1}
              onChange={(event) => setPerQueryLimit(event.target.value)}
              title="每条 Query 抓取 URL 数"
              type="number"
              value={perQueryLimit}
            />
          ) : null}
          {isRunning ? (
            <button
              className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-rose-300 hover:text-rose-700"
              onClick={handleStop}
              type="button"
            >
              <StopIcon />
              停止
            </button>
          ) : (
            <button
              className="inline-flex h-9 items-center gap-1 rounded-md bg-teal-600 px-3 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              disabled={!canStart}
              onClick={() => void handleStart()}
              type="button"
            >
              <PlayIcon />
              开始仅抓取
            </button>
          )}
          <button
            className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-slate-300"
            disabled={!artifact?.artifactId}
            onClick={() => artifact?.artifactId && void downloadCrawlOnlyArtifact(artifact.artifactId, 'markdown')}
            type="button"
          >
            <DownloadIcon />
            MD
          </button>
          <button
            className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-slate-300"
            disabled={!artifact?.artifactId}
            onClick={() => artifact?.artifactId && void downloadCrawlOnlyArtifact(artifact.artifactId, 'json')}
            type="button"
          >
            <DownloadIcon />
            JSON
          </button>
        </div>
      </div>

      <div className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-sm font-medium text-slate-600">{message}</div>
      {error ? <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">{error}</div> : null}

      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        <Metric label="总 URL" value={summary.totalPosts} />
        <Metric label="已处理" value={summary.processedPosts} />
        <Metric label="成功" value={summary.successCount} />
        <Metric label="跳过" value={summary.skippedCount} />
        <Metric label="失败" value={summary.failedCount} />
      </div>

      {Object.keys(environmentStates).length > 0 ? (
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(environmentStates).map(([environmentId, state]) => (
            <div className="rounded-md border border-slate-200 px-3 py-2 text-sm" key={environmentId}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold text-slate-900">
                  环境 {state.environmentIndex} · {environmentId}
                </span>
                <span className={state.status === 'completed' ? 'text-xs font-semibold text-emerald-700' : 'text-xs font-semibold text-teal-700'}>
                  {state.status === 'completed' ? '完成' : '运行中'}
                </span>
              </div>
              <div className="mt-1 text-xs text-slate-500">{state.totalPosts} 条 URL · {state.userId}</div>
            </div>
          ))}
        </div>
      ) : null}

      {Object.keys(postStates).length > 0 ? (
        <div className="mt-4 max-h-80 overflow-y-auto rounded-md border border-slate-200">
          <table className="w-full table-fixed text-left text-sm">
            <thead className="sticky top-0 bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="w-24 px-3 py-2">状态</th>
                <th className="px-3 py-2">帖子</th>
                <th className="w-36 px-3 py-2">社区</th>
                <th className="w-56 px-3 py-2">原因</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {Object.entries(postStates).map(([postUrl, state]) => (
                <tr className="align-top" key={postUrl}>
                  <td className="px-3 py-2">
                    <span className={postStatusClassName(state.status)}>{postStatusLabel(state.status)}</span>
                  </td>
                  <td className="min-w-0 px-3 py-2">
                    <a className="line-clamp-2 font-medium text-slate-950 underline decoration-slate-300 underline-offset-2 hover:text-teal-700" href={postUrl} rel="noreferrer" target="_blank" title={state.title}>
                      {state.title || postUrl}
                    </a>
                    <div className="mt-1 truncate text-xs text-slate-500">{state.environmentId || '-'}</div>
                  </td>
                  <td className="truncate px-3 py-2 text-slate-600">{state.subreddit || '-'}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">{state.reason || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}

function createInitialPostStates(searchResults: RedditSearchResultItem[]): Record<string, PostState> {
  return Object.fromEntries(
    searchResults.map((item) => [
      item.postUrl,
      {
        status: 'pending',
        title: item.title,
        subreddit: item.subreddit,
      } satisfies PostState,
    ]),
  )
}

function summarizeResults(results: CrawlOnlyResult[], states: Record<string, PostState>) {
  const totalPosts = Object.keys(states).length || results.length
  return {
    totalPosts,
    processedPosts: results.length,
    successCount: results.filter((item) => item.status === 'success').length,
    skippedCount: results.filter((item) => item.status === 'skipped').length,
    failedCount: results.filter((item) => item.status === 'failed').length,
  }
}

function markRunningPostsSkipped(states: Record<string, PostState>): Record<string, PostState> {
  return Object.fromEntries(
    Object.entries(states).map(([postUrl, state]) => [
      postUrl,
      state.status === 'pending' || state.status === 'running'
        ? { ...state, status: 'skipped', reason: '任务已停止，未继续处理' }
        : state,
    ]),
  )
}

function clampNumber(value: string, min: number, max: number, fallback: number): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    return fallback
  }
  return Math.min(max, Math.max(min, Math.trunc(parsed)))
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md bg-slate-50 px-3 py-2">
      <div className="text-xs font-semibold text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-950">{value}</div>
    </div>
  )
}

function postStatusLabel(status: PostState['status']): string {
  const labels: Record<PostState['status'], string> = {
    pending: '等待',
    running: '抓取中',
    success: '成功',
    skipped: '跳过',
    failed: '失败',
  }
  return labels[status]
}

function postStatusClassName(status: PostState['status']): string {
  const base = 'inline-flex rounded-md px-2 py-1 text-xs font-semibold'
  if (status === 'success') return `${base} bg-emerald-50 text-emerald-700`
  if (status === 'running') return `${base} bg-teal-50 text-teal-700`
  if (status === 'skipped') return `${base} bg-amber-50 text-amber-700`
  if (status === 'failed') return `${base} bg-rose-50 text-rose-700`
  return `${base} bg-slate-100 text-slate-600`
}
