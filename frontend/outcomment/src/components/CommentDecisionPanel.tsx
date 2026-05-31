import { useEffect, useMemo, useRef, useState } from 'react'

import { streamCommentDecisions } from '../lib/api'
import { downloadCommentDecisionsCsv, downloadCommentDecisionsXlsx } from '../lib/excel'
import type {
  ApprovedQueryPlan,
  CommentDecisionResult,
  CommentDecisionStreamEvent,
  CommentDecisionSummary,
  CommentLengthDistribution,
  DecisionEnvironmentState,
  DecisionPostState,
  RedditSearchResultItem,
} from '../types'
import { DownloadIcon, PlayIcon, StopIcon } from './icons'

type CommentDecisionPanelProps = {
  approvedPlan: ApprovedQueryPlan
  searchResults: RedditSearchResultItem[]
}

export function CommentDecisionPanel({ approvedPlan, searchResults }: CommentDecisionPanelProps) {
  const [isDeciding, setIsDeciding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [maxSuggestions, setMaxSuggestions] = useState('')
  const [lengthDistribution, setLengthDistribution] = useState<CommentLengthDistribution>({
    short: 30,
    medium: 50,
    long: 20,
  })
  const [summary, setSummary] = useState<CommentDecisionSummary | null>(null)
  const [results, setResults] = useState<CommentDecisionResult[]>([])
  const [postStates, setPostStates] = useState<Record<string, DecisionPostState>>(() =>
    createInitialPostStates(searchResults),
  )
  const [environmentStates, setEnvironmentStates] = useState<Record<string, DecisionEnvironmentState>>({})
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const successfulResults = useMemo(
    () => results.filter((result) => result.status === 'success' && result.commentUrl && result.commentText),
    [results],
  )
  const currentSummary = useMemo(() => summary ?? summarizePostStates(postStates), [postStates, summary])
  const canStart = searchResults.length > 0 && !isDeciding

  const handleStart = async () => {
    const controller = new AbortController()
    abortRef.current = controller
    setIsDeciding(true)
    setError(null)
    setSummary(null)
    setResults([])
    setEnvironmentStates({})
    setPostStates(createInitialPostStates(searchResults))

    try {
      await streamCommentDecisions({
        payload: {
          productContext: approvedPlan.productContext,
          queries: approvedPlan.queries,
          searchResults,
          maxSuggestions: parseMaxSuggestions(maxSuggestions),
          commentLengthDistribution: lengthDistribution,
        },
        signal: controller.signal,
        onEvent: applyDecisionEvent,
      })
    } catch (exc) {
      if (controller.signal.aborted) {
        setError('评论决策已停止。')
        setPostStates((current) => markRunningPostsSkipped(current))
      } else {
        setError(exc instanceof Error ? exc.message : '评论决策失败')
      }
    } finally {
      setIsDeciding(false)
      abortRef.current = null
    }
  }

  const handleStop = () => {
    abortRef.current?.abort()
    setIsDeciding(false)
  }

  const applyDecisionEvent = (event: CommentDecisionStreamEvent) => {
    if (event.type === 'decision_started') {
      setSummary({
        totalPosts: event.totalPosts,
        processedPosts: 0,
        successCount: 0,
        skippedCount: 0,
        failedCount: 0,
      })
      return
    }

    if (event.type === 'environment_started') {
      setEnvironmentStates((current) => ({
        ...current,
        [event.environmentId]: {
          status: 'running',
          environmentIndex: event.environmentIndex,
          userId: event.userId,
          totalPosts: event.totalPosts,
          processed: 0,
          success: 0,
          skipped: 0,
          failed: 0,
        },
      }))
      return
    }

    if (event.type === 'post_started') {
      updatePostState(event.postUrl, {
        status: 'detail',
        environmentId: event.environmentId,
        title: event.title,
      })
      return
    }

    if (event.type === 'detail_collected') {
      updatePostState(event.postUrl, {
        status: 'detail',
        environmentId: event.environmentId,
        title: event.title,
        subreddit: event.subreddit,
        reason: `已抓取 ${event.commentCount} 条首屏评论，媒体 ${event.mediaCount} 个`,
      })
      return
    }

    if (event.type === 'gemini_started') {
      updatePostState(event.postUrl, {
        status: 'gemini',
        environmentId: event.environmentId,
        reason: 'Gemini 评论决策中',
      })
      return
    }

    if (event.type === 'post_result') {
      const result = event.result
      setPostStates((current) => ({
        ...current,
        [result.postUrl]: {
          ...(current[result.postUrl] ?? fallbackPostState(result)),
          status: result.status,
          title: result.title || current[result.postUrl]?.title || '',
          subreddit: result.subreddit || current[result.postUrl]?.subreddit || '',
          sourceQuery: result.sourceQuery || current[result.postUrl]?.sourceQuery || '',
          reason: result.reason,
          environmentId: event.environmentId,
          commentUrl: result.commentUrl,
          commentText: result.commentText,
        },
      }))
      setResults((current) => mergeDecisionResult(current, result))
      return
    }

    if (event.type === 'environment_finished') {
      setEnvironmentStates((current) => ({
        ...current,
        [event.environmentId]: {
          status: 'completed',
          environmentIndex: event.environmentIndex,
          userId: event.userId,
          totalPosts: event.totalPosts,
          processed: event.processed,
          success: event.success,
          skipped: event.skipped,
          failed: event.failed,
        },
      }))
      return
    }

    if (event.type === 'done') {
      setSummary(event.summary)
      setResults(event.results)
      return
    }

    if (event.type === 'error') {
      setError(event.message)
    }
  }

  const updatePostState = (postUrl: string, patch: Partial<DecisionPostState>) => {
    setPostStates((current) => ({
      ...current,
      [postUrl]: {
        ...(current[postUrl] ?? fallbackPostState({ postUrl, sourceQuery: '', status: 'skipped' })),
        ...patch,
      },
    }))
  }

  return (
    <section className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-950">评论决策生成</h2>
          <p className="mt-1 text-sm text-slate-500">打开去重 URL，抓取帖子和首屏评论，并生成评论链接与评论内容。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="h-9 w-32 rounded-md border border-slate-300 px-2 text-sm outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-100"
            disabled={isDeciding}
            min={1}
            onChange={(event) => setMaxSuggestions(event.target.value)}
            placeholder="最多建议数"
            type="number"
            value={maxSuggestions}
          />
          {isDeciding ? (
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
              生成评论决策
            </button>
          )}
          <button
            className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-slate-300"
            disabled={!successfulResults.length}
            onClick={() => downloadCommentDecisionsCsv(successfulResults)}
            type="button"
          >
            <DownloadIcon />
            CSV
          </button>
          <button
            className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-slate-300"
            disabled={!successfulResults.length}
            onClick={() => downloadCommentDecisionsXlsx(successfulResults)}
            type="button"
          >
            <DownloadIcon />
            XLSX
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">
          {error}
        </div>
      )}

      <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-950">评论长度分布</h3>
            <p className="mt-1 text-xs text-slate-500">后端会按这个概率为每条帖子随机选择短、中、长评论指令。</p>
          </div>
          <div className="text-xs font-semibold text-slate-600">
            短 {lengthDistribution.short}% · 中 {lengthDistribution.medium}% · 长 {lengthDistribution.long}%
          </div>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <LengthSlider
            disabled={isDeciding}
            label="短评论比例"
            max={100}
            value={lengthDistribution.short}
            onChange={(value) => {
              const short = value
              const medium = Math.min(lengthDistribution.medium, 100 - short)
              setLengthDistribution({ short, medium, long: 100 - short - medium })
            }}
          />
          <LengthSlider
            disabled={isDeciding}
            label="中评论比例"
            max={100 - lengthDistribution.short}
            value={lengthDistribution.medium}
            onChange={(medium) =>
              setLengthDistribution({
                short: lengthDistribution.short,
                medium,
                long: 100 - lengthDistribution.short - medium,
              })
            }
          />
          <LengthSlider disabled label="长评论比例" max={100} value={lengthDistribution.long} />
        </div>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        <Metric label="总 URL" value={currentSummary.totalPosts} />
        <Metric label="已处理" value={currentSummary.processedPosts} />
        <Metric label="成功建议" value={currentSummary.successCount} />
        <Metric label="跳过" value={currentSummary.skippedCount} />
        <Metric label="失败" value={currentSummary.failedCount} />
      </div>

      {Object.keys(environmentStates).length > 0 && (
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(environmentStates).map(([environmentId, state]) => (
            <div className="rounded-md border border-slate-200 px-3 py-2 text-sm" key={environmentId}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold text-slate-900">
                  环境 {state.environmentIndex} · {environmentId}
                </span>
                <span className={environmentStatusClassName(state.status)}>{environmentStatusLabel(state.status)}</span>
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {state.processed}/{state.totalPosts} · 成功 {state.success} · 跳过 {state.skipped} · 失败 {state.failed}
              </div>
            </div>
          ))}
        </div>
      )}

      {successfulResults.length > 0 && (
        <div className="mt-4 overflow-x-auto rounded-md border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2">commentUrl</th>
                <th className="px-3 py-2">commentText</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {successfulResults.map((result) => (
                <tr key={`${result.postUrl}-${result.commentUrl}`} className="align-top">
                  <td className="max-w-[420px] px-3 py-2">
                    <a
                      className="break-words font-medium text-teal-700 underline decoration-teal-200 underline-offset-2 hover:text-teal-800"
                      href={result.commentUrl ?? undefined}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {result.commentUrl}
                    </a>
                  </td>
                  <td className="min-w-[520px] whitespace-pre-wrap px-3 py-2 text-slate-700">{result.commentText}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

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
                  <a
                    className="line-clamp-2 font-medium text-slate-950 underline decoration-slate-300 underline-offset-2 hover:text-teal-700"
                    href={postUrl}
                    rel="noreferrer"
                    target="_blank"
                    title={state.title}
                  >
                    {state.title || postUrl}
                  </a>
                  <div className="mt-1 truncate text-xs text-slate-500">{state.sourceQuery}</div>
                </td>
                <td className="truncate px-3 py-2 text-slate-600">{state.subreddit || '-'}</td>
                <td className="px-3 py-2 text-xs text-slate-500">{state.reason || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function createInitialPostStates(searchResults: RedditSearchResultItem[]): Record<string, DecisionPostState> {
  return Object.fromEntries(
    searchResults.map((item) => [
      item.postUrl,
      {
        status: 'pending',
        title: item.title,
        subreddit: item.subreddit,
        sourceQuery: item.query,
      } satisfies DecisionPostState,
    ]),
  )
}

function summarizePostStates(states: Record<string, DecisionPostState>): CommentDecisionSummary {
  const values = Object.values(states)
  return {
    totalPosts: values.length,
    processedPosts: values.filter((item) => ['success', 'skipped', 'failed'].includes(item.status)).length,
    successCount: values.filter((item) => item.status === 'success').length,
    skippedCount: values.filter((item) => item.status === 'skipped').length,
    failedCount: values.filter((item) => item.status === 'failed').length,
  }
}

function markRunningPostsSkipped(states: Record<string, DecisionPostState>): Record<string, DecisionPostState> {
  return Object.fromEntries(
    Object.entries(states).map(([postUrl, state]) => [
      postUrl,
      ['pending', 'detail', 'gemini'].includes(state.status)
        ? { ...state, status: 'skipped', reason: '任务已停止，未继续处理' }
        : state,
    ]),
  )
}

function mergeDecisionResult(results: CommentDecisionResult[], next: CommentDecisionResult): CommentDecisionResult[] {
  const rest = results.filter((item) => item.postUrl !== next.postUrl)
  return next.status === 'success' ? [...rest, next] : rest
}

function fallbackPostState(result: Pick<CommentDecisionResult, 'postUrl' | 'sourceQuery' | 'status'>): DecisionPostState {
  return {
    status: result.status === 'success' ? 'success' : result.status === 'failed' ? 'failed' : 'skipped',
    title: result.postUrl,
    subreddit: '',
    sourceQuery: result.sourceQuery,
  }
}

function parseMaxSuggestions(value: string): number | undefined {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : undefined
}

function LengthSlider({
  disabled = false,
  label,
  max,
  value,
  onChange,
}: {
  disabled?: boolean
  label: string
  max: number
  value: number
  onChange?: (value: number) => void
}) {
  return (
    <label className="block">
      <div className="mb-1 flex items-center justify-between gap-2 text-xs font-semibold text-slate-600">
        <span>{label}</span>
        <span>{value}%</span>
      </div>
      <input
        className="w-full accent-teal-600 disabled:opacity-60"
        disabled={disabled || !onChange}
        max={max}
        min={0}
        onChange={(event) => onChange?.(Number(event.target.value))}
        step={1}
        type="range"
        value={value}
      />
    </label>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md bg-slate-50 px-3 py-2">
      <div className="text-xs font-semibold text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-950">{value}</div>
    </div>
  )
}

function postStatusLabel(status: DecisionPostState['status']): string {
  const labels: Record<DecisionPostState['status'], string> = {
    pending: '等待',
    detail: '抓详情',
    gemini: '生成中',
    success: '成功',
    skipped: '跳过',
    failed: '失败',
  }
  return labels[status]
}

function postStatusClassName(status: DecisionPostState['status']): string {
  const base = 'inline-flex rounded-md px-2 py-1 text-xs font-semibold'
  if (status === 'success') return `${base} bg-emerald-50 text-emerald-700`
  if (status === 'detail' || status === 'gemini') return `${base} bg-teal-50 text-teal-700`
  if (status === 'skipped') return `${base} bg-amber-50 text-amber-700`
  if (status === 'failed') return `${base} bg-rose-50 text-rose-700`
  return `${base} bg-slate-100 text-slate-600`
}

function environmentStatusLabel(status: DecisionEnvironmentState['status']): string {
  const labels: Record<DecisionEnvironmentState['status'], string> = {
    starting: '启动中',
    running: '运行中',
    completed: '完成',
    failed: '失败',
  }
  return labels[status]
}

function environmentStatusClassName(status: DecisionEnvironmentState['status']): string {
  const base = 'text-xs font-semibold'
  if (status === 'completed') return `${base} text-emerald-700`
  if (status === 'failed') return `${base} text-rose-700`
  return `${base} text-teal-700`
}
