import { useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { generateQueryPlan, streamRedditSearch } from '../lib/api'
import { downloadRedditSearchResultsCsv, downloadRedditSearchResultsXlsx } from '../lib/excel'
import type {
  ApprovedQueryPlan,
  PlannedQuery,
  PlannedQueryPayload,
  ProductContext,
  QuerySearchState,
  QueryIntent,
  RedditSearchResultItem,
  RedditSearchStreamEvent,
  RedditSearchSummary,
  SuggestedTimeRange,
} from '../types'
import { CheckIcon, DownloadIcon, PlayIcon, PlusIcon, SparkIcon, StopIcon, TrashIcon } from './icons'
import { CommentDecisionPanel } from './CommentDecisionPanel'

const DEFAULT_CONTEXT: ProductContext = {
  productName: '',
  productDescription: '',
  targetAudience: '',
  sellingPoints: '',
  competitors: '',
  commentRequirements: '',
  forbiddenTopics: '',
  desiredQueryCount: 20,
}

const INTENT_OPTIONS: Array<{ value: QueryIntent; label: string }> = [
  { value: 'pain_point', label: '痛点' },
  { value: 'recommendation', label: '推荐请求' },
  { value: 'review', label: '评测口碑' },
  { value: 'alternative', label: '替代品' },
  { value: 'comparison', label: '对比' },
  { value: 'problem_solution', label: '问题解决' },
  { value: 'community_discussion', label: '社区讨论' },
  { value: 'other', label: '其他' },
]

const TIME_RANGE_OPTIONS: Array<{ value: SuggestedTimeRange; label: string }> = [
  { value: 'week', label: 'Past week' },
  { value: 'month', label: 'Past month' },
  { value: 'all', label: 'All time' },
]

const EMPTY_QUERY: PlannedQueryPayload = {
  query: '',
  intent: 'pain_point',
  reason: '',
  priority: 3,
  suggestedTimeRange: 'week',
}

export function QueryPlanWorkspace() {
  const [context, setContext] = useState<ProductContext>(DEFAULT_CONTEXT)
  const [queries, setQueries] = useState<PlannedQuery[]>([])
  const [approvedPlan, setApprovedPlan] = useState<ApprovedQueryPlan | null>(null)
  const [isGenerating, setIsGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [querySearchStates, setQuerySearchStates] = useState<Record<string, QuerySearchState>>({})
  const [searchResults, setSearchResults] = useState<RedditSearchResultItem[]>([])
  const [searchSummary, setSearchSummary] = useState<RedditSearchSummary | null>(null)
  const [isSearching, setIsSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const searchAbortRef = useRef<AbortController | null>(null)

  const validQueryCount = useMemo(
    () => queries.filter((item) => item.query.trim() && item.reason.trim()).length,
    [queries],
  )
  const canGenerate = context.productName.trim() && context.productDescription.trim() && !isGenerating
  const canApprove = validQueryCount > 0 && !isGenerating
  const canStartSearch = Boolean(approvedPlan?.queries.length) && !isGenerating && !isSearching

  const resetSearchState = () => {
    searchAbortRef.current?.abort()
    searchAbortRef.current = null
    setIsSearching(false)
    setSearchError(null)
    setQuerySearchStates({})
    setSearchResults([])
    setSearchSummary(null)
  }

  const updateContext = <K extends keyof ProductContext>(key: K, value: ProductContext[K]) => {
    setContext((current) => ({ ...current, [key]: value }))
    setError(null)
    setApprovedPlan(null)
    resetSearchState()
  }

  const handleGenerate = async () => {
    if (!context.productName.trim() || !context.productDescription.trim()) {
      setError('产品名称和产品情况是必填项。')
      return
    }

    setIsGenerating(true)
    setError(null)
    setApprovedPlan(null)
    resetSearchState()

    try {
      const response = await generateQueryPlan({
        ...context,
        productName: context.productName.trim(),
        productDescription: context.productDescription.trim(),
      })
      setQueries(response.queries.map(createPlannedQuery))
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '生成 Query 失败')
    } finally {
      setIsGenerating(false)
    }
  }

  const updateQuery = <K extends keyof PlannedQuery>(id: string, key: K, value: PlannedQuery[K]) => {
    setQueries((current) => current.map((item) => (item.id === id ? { ...item, [key]: value } : item)))
    setApprovedPlan(null)
    resetSearchState()
  }

  const addQuery = () => {
    setQueries((current) => [...current, createPlannedQuery(EMPTY_QUERY)])
    setApprovedPlan(null)
    resetSearchState()
  }

  const removeQuery = (id: string) => {
    setQueries((current) => current.filter((item) => item.id !== id))
    setApprovedPlan(null)
    resetSearchState()
  }

  const approveQueries = () => {
    const approvedQueries = queries
      .map((item) => ({
        ...item,
        query: item.query.trim(),
        reason: item.reason.trim(),
      }))
      .filter((item) => item.query && item.reason)
      .sort((left, right) => left.priority - right.priority || left.query.localeCompare(right.query))

    if (!approvedQueries.length) {
      setError('至少需要一条有效 Query 才能批准。')
      return
    }

    setQueries(approvedQueries)
    setApprovedPlan({
      productContext: { ...context },
      queries: approvedQueries,
      approvedAt: new Date().toISOString(),
    })
    setQuerySearchStates(createInitialSearchStates(approvedQueries))
    setSearchResults([])
    setSearchSummary(null)
    setSearchError(null)
    setError(null)
  }

  const handleStartSearch = async () => {
    if (!approvedPlan) {
      setSearchError('请先批准 Query 列表。')
      return
    }

    const controller = new AbortController()
    searchAbortRef.current = controller
    setIsSearching(true)
    setSearchError(null)
    setSearchResults([])
    setSearchSummary(null)
    setQuerySearchStates(createInitialSearchStates(approvedPlan.queries))

    try {
      await streamRedditSearch({
        payload: {
          productContext: approvedPlan.productContext,
          queries: approvedPlan.queries,
          perQueryLimit: 20,
          searchSort: 'relevance',
        },
        signal: controller.signal,
        onEvent: (event) => applySearchEvent(event, approvedPlan),
      })
    } catch (exc) {
      if (controller.signal.aborted) {
        setSearchError('搜索已取消。')
      } else {
        setSearchError(exc instanceof Error ? exc.message : 'Reddit 搜索失败')
      }
    } finally {
      setIsSearching(false)
      searchAbortRef.current = null
    }
  }

  const handleStopSearch = () => {
    searchAbortRef.current?.abort()
    setIsSearching(false)
  }

  const applySearchEvent = (event: RedditSearchStreamEvent, plan: ApprovedQueryPlan) => {
    if (event.type === 'query_started') {
      const query = plan.queries[event.queryIndex - 1]
      if (!query) {
        return
      }
      setQuerySearchStates((current) => ({
        ...current,
        [query.id]: { status: 'running', rawResultCount: 0, uniqueResultCount: 0 },
      }))
      return
    }

    if (event.type === 'query_result') {
      const query = plan.queries[event.queryIndex - 1]
      if (!query) {
        return
      }
      setQuerySearchStates((current) => ({
        ...current,
        [query.id]: {
          status: event.status,
          reason: event.reason,
          rawResultCount: event.rawResultCount,
          uniqueResultCount: event.uniqueResultCount,
        },
      }))
      return
    }

    if (event.type === 'summary' || event.type === 'done') {
      setSearchSummary(event.summary)
      setSearchResults(event.results)
      return
    }

    if (event.type === 'error') {
      setSearchError(event.message)
    }
  }

  return (
    <div className="mx-auto grid w-full max-w-[1600px] gap-4 px-4 py-4 lg:grid-cols-[400px_minmax(0,1fr)] lg:px-6">
      <aside className="min-w-0 space-y-3 rounded-md border border-slate-200 bg-white p-3.5 shadow-sm lg:sticky lg:top-4 lg:max-h-[calc(100vh-32px)] lg:overflow-y-auto">
        <div>
          <h2 className="text-base font-semibold text-slate-950">产品上下文</h2>
          <p className="mt-1 text-sm text-slate-500">用于裂解 Reddit 搜索短语，本轮不会触发 RPA。</p>
        </div>

        <Field label="产品名称" required>
          <input
            className={inputClassName}
            onChange={(event) => updateContext('productName', event.target.value)}
            placeholder="例如：AI video editor"
            value={context.productName}
          />
        </Field>

        <Field label="产品情况" required>
          <textarea
            className={`${inputClassName} min-h-28 resize-y leading-6`}
            onChange={(event) => updateContext('productDescription', event.target.value)}
            placeholder="产品解决什么问题、核心功能、使用场景、价格/地区等"
            value={context.productDescription}
          />
        </Field>

        <Field label="目标用户">
          <textarea
            className={`${inputClassName} min-h-20 resize-y leading-6`}
            onChange={(event) => updateContext('targetAudience', event.target.value)}
            placeholder="用户画像、行业、痛点、使用动机"
            value={context.targetAudience}
          />
        </Field>

        <Field label="卖点">
          <textarea
            className={`${inputClassName} min-h-20 resize-y leading-6`}
            onChange={(event) => updateContext('sellingPoints', event.target.value)}
            placeholder="希望自然带出的优势"
            value={context.sellingPoints}
          />
        </Field>

        <Field label="竞品">
          <textarea
            className={`${inputClassName} min-h-16 resize-y leading-6`}
            onChange={(event) => updateContext('competitors', event.target.value)}
            placeholder="竞品、替代方案、常被比较的品牌"
            value={context.competitors}
          />
        </Field>

        <Field label="评论要求">
          <textarea
            className={`${inputClassName} min-h-24 resize-y leading-6`}
            onChange={(event) => updateContext('commentRequirements', event.target.value)}
            placeholder="语气、长度、是否允许提品牌、是否需要移动端口吻"
            value={context.commentRequirements}
          />
        </Field>

        <Field label="禁忌点">
          <textarea
            className={`${inputClassName} min-h-16 resize-y leading-6`}
            onChange={(event) => updateContext('forbiddenTopics', event.target.value)}
            placeholder="不要提及的卖点、地区、价格、竞品或表达方式"
            value={context.forbiddenTopics}
          />
        </Field>

        <Field label="期望 Query 数量">
          <input
            className={inputClassName}
            max={50}
            min={5}
            onChange={(event) =>
              updateContext('desiredQueryCount', clampQueryCount(Number(event.target.value) || 20))
            }
            type="number"
            value={context.desiredQueryCount}
          />
        </Field>

        <button
          className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-teal-600 px-4 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          disabled={!canGenerate}
          onClick={() => void handleGenerate()}
          type="button"
        >
          <SparkIcon />
          {isGenerating ? '生成中...' : '生成搜索 Query'}
        </button>
      </aside>

      <section className="min-w-0 space-y-4">
        <div className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-slate-950">Query 审核列表</h2>
              <p className="mt-1 text-sm text-slate-500">生成后可人工编辑、删除、补充和批准。</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-700">
                有效 {validQueryCount} / 全部 {queries.length}
              </span>
              <button
                className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700"
                onClick={addQuery}
                type="button"
              >
                <PlusIcon />
                新增
              </button>
              <button
                className="inline-flex h-9 items-center gap-1 rounded-md bg-slate-950 px-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                disabled={!canApprove}
                onClick={approveQueries}
                type="button"
              >
                <CheckIcon />
                批准 Query 列表
              </button>
            </div>
          </div>

          {error && (
            <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">
              {error}
            </div>
          )}

          {approvedPlan && (
            <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700">
              已批准 {approvedPlan.queries.length} 条 Query。可以执行 Reddit 搜索并汇总去重帖子 URL。
            </div>
          )}
        </div>

        {approvedPlan && (
          <section className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">Reddit URL 搜索汇总</h2>
                <p className="mt-1 text-sm text-slate-500">按每条 Query 的时间范围执行搜索，默认每条抓取 Top 20。</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-md bg-teal-600 px-3 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                  disabled={!canStartSearch}
                  onClick={() => void handleStartSearch()}
                  type="button"
                >
                  <PlayIcon />
                  {isSearching ? '搜索中...' : '执行 Reddit 搜索'}
                </button>
                {isSearching && (
                  <button
                    className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-rose-300 hover:text-rose-700"
                    onClick={handleStopSearch}
                    type="button"
                  >
                    <StopIcon />
                    停止
                  </button>
                )}
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-slate-300"
                  disabled={!searchResults.length}
                  onClick={() => downloadRedditSearchResultsCsv(approvedPlan.productContext, searchResults)}
                  type="button"
                >
                  <DownloadIcon />
                  CSV
                </button>
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-slate-300"
                  disabled={!searchResults.length}
                  onClick={() => downloadRedditSearchResultsXlsx(approvedPlan.productContext, searchResults)}
                  type="button"
                >
                  <DownloadIcon />
                  XLSX
                </button>
              </div>
            </div>

            {searchError && (
              <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">
                {searchError}
              </div>
            )}

            <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
              <Metric label="Query 总数" value={searchSummary?.totalQueries ?? approvedPlan.queries.length} />
              <Metric label="成功 Query" value={searchSummary?.successfulQueries ?? countSearchStatus(querySearchStates, 'success')} />
              <Metric label="失败 Query" value={searchSummary?.failedQueries ?? countFailedSearchStates(querySearchStates)} />
              <Metric label="原始 URL" value={searchSummary?.rawUrlCount ?? 0} />
              <Metric label="去重 URL" value={searchSummary?.uniqueUrlCount ?? searchResults.length} />
            </div>

            <div className="mt-3 grid gap-2">
              {approvedPlan.queries.map((item, index) => {
                const state = querySearchStates[item.id] ?? {
                  status: 'pending',
                  rawResultCount: 0,
                  uniqueResultCount: 0,
                }
                return (
                  <div
                    className="grid gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm md:grid-cols-[40px_minmax(0,1fr)_110px_110px_120px]"
                    key={item.id}
                  >
                    <span className="font-semibold text-slate-500">#{index + 1}</span>
                    <span className="truncate font-medium text-slate-900" title={item.query}>
                      {item.query}
                    </span>
                    <span className="text-slate-500">{timeRangeLabel(item.suggestedTimeRange)}</span>
                    <span className={statusClassName(state.status)}>{statusLabel(state.status)}</span>
                    <span className="text-slate-500">
                      {state.rawResultCount} / {state.uniqueResultCount} URL
                    </span>
                    {state.reason && state.status === 'failed' ? (
                      <span className="md:col-span-5 text-xs text-rose-600">{state.reason}</span>
                    ) : null}
                  </div>
                )
              })}
            </div>

            {searchResults.length > 0 && (
              <div className="mt-4 overflow-x-auto rounded-md border border-slate-200">
                <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                  <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-3 py-2">优先级</th>
                      <th className="px-3 py-2">来源 Query</th>
                      <th className="px-3 py-2">Subreddit</th>
                      <th className="px-3 py-2">标题</th>
                      <th className="px-3 py-2">互动</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 bg-white">
                    {searchResults.map((item) => (
                      <tr key={item.postUrl} className="align-top">
                        <td className="whitespace-nowrap px-3 py-2 font-semibold text-slate-700">P{item.priority}</td>
                        <td className="max-w-64 px-3 py-2 text-slate-600">
                          <div className="line-clamp-2">{item.query}</div>
                          {item.matchedQueries.length > 1 && (
                            <div className="mt-1 text-xs text-teal-700">命中 {item.matchedQueries.length} 个 Query</div>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-slate-600">{item.subreddit}</td>
                        <td className="min-w-[360px] px-3 py-2">
                          <a
                            className="font-medium text-slate-950 underline decoration-slate-300 underline-offset-2 hover:text-teal-700"
                            href={item.postUrl}
                            rel="noreferrer"
                            target="_blank"
                          >
                            {item.title}
                          </a>
                          <div className="mt-1 text-xs text-slate-500">{item.ageText || item.postId}</div>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-slate-600">
                          {item.votes ?? '-'} votes · {item.comments ?? '-'} comments
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}

        {approvedPlan && searchResults.length > 0 && (
          <CommentDecisionPanel
            approvedPlan={approvedPlan}
            key={searchResults.map((item) => item.postUrl).join('|')}
            searchResults={searchResults}
          />
        )}

        {queries.length === 0 ? (
          <div className="rounded-md border border-dashed border-slate-300 bg-white px-5 py-12 text-center text-sm font-medium text-slate-500">
            请输入产品上下文并生成 Query
          </div>
        ) : (
          <div className="grid min-w-0 gap-3">
            {queries.map((item, index) => (
              <article className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm" key={item.id}>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-slate-500">#{index + 1}</span>
                  <button
                    aria-label="删除 Query"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition hover:bg-rose-50 hover:text-rose-600"
                    onClick={() => removeQuery(item.id)}
                    title="删除 Query"
                    type="button"
                  >
                    <TrashIcon />
                  </button>
                </div>

                <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_180px_150px_120px]">
                  <label className="block min-w-0">
                    <span className="text-xs font-semibold text-slate-500">搜索 Query</span>
                    <input
                      className={`${inputClassName} mt-1`}
                      onChange={(event) => updateQuery(item.id, 'query', event.target.value)}
                      value={item.query}
                    />
                  </label>

                  <label className="block">
                    <span className="text-xs font-semibold text-slate-500">意图</span>
                    <select
                      className={`${inputClassName} mt-1`}
                      onChange={(event) => updateQuery(item.id, 'intent', event.target.value as QueryIntent)}
                      value={item.intent}
                    >
                      {INTENT_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="block">
                    <span className="text-xs font-semibold text-slate-500">时间范围</span>
                    <select
                      className={`${inputClassName} mt-1`}
                      onChange={(event) =>
                        updateQuery(item.id, 'suggestedTimeRange', event.target.value as SuggestedTimeRange)
                      }
                      value={item.suggestedTimeRange}
                    >
                      {TIME_RANGE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="block">
                    <span className="text-xs font-semibold text-slate-500">优先级</span>
                    <select
                      className={`${inputClassName} mt-1`}
                      onChange={(event) => updateQuery(item.id, 'priority', Number(event.target.value))}
                      value={item.priority}
                    >
                      {[1, 2, 3, 4, 5].map((priority) => (
                        <option key={priority} value={priority}>
                          P{priority}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <label className="mt-3 block">
                  <span className="text-xs font-semibold text-slate-500">生成理由</span>
                  <textarea
                    className={`${inputClassName} mt-1 min-h-16 resize-y leading-6`}
                    onChange={(event) => updateQuery(item.id, 'reason', event.target.value)}
                    value={item.reason}
                  />
                </label>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function Field({
  label,
  required = false,
  children,
}: {
  label: string
  required?: boolean
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-600">
        {label}
        {required ? <span className="text-rose-500"> *</span> : null}
      </span>
      <div className="mt-1">{children}</div>
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

function createInitialSearchStates(queries: PlannedQuery[]): Record<string, QuerySearchState> {
  return Object.fromEntries(
    queries.map((query) => [
      query.id,
      {
        status: 'pending',
        rawResultCount: 0,
        uniqueResultCount: 0,
      } satisfies QuerySearchState,
    ]),
  )
}

function countSearchStatus(states: Record<string, QuerySearchState>, status: QuerySearchState['status']): number {
  return Object.values(states).filter((state) => state.status === status).length
}

function countFailedSearchStates(states: Record<string, QuerySearchState>): number {
  return Object.values(states).filter((state) => state.status === 'failed' || state.status === 'no_results').length
}

function statusLabel(status: QuerySearchState['status']): string {
  const labels: Record<QuerySearchState['status'], string> = {
    pending: '等待',
    running: '搜索中',
    success: '成功',
    no_results: '无结果',
    failed: '失败',
  }
  return labels[status]
}

function statusClassName(status: QuerySearchState['status']): string {
  const base = 'font-semibold'
  if (status === 'success') {
    return `${base} text-emerald-700`
  }
  if (status === 'running') {
    return `${base} text-teal-700`
  }
  if (status === 'failed' || status === 'no_results') {
    return `${base} text-rose-700`
  }
  return `${base} text-slate-500`
}

function timeRangeLabel(value: SuggestedTimeRange): string {
  return TIME_RANGE_OPTIONS.find((option) => option.value === value)?.label ?? value
}

function createPlannedQuery(payload: PlannedQueryPayload): PlannedQuery {
  return {
    id: createId(),
    ...payload,
  }
}

function createId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function clampQueryCount(value: number): number {
  return Math.min(50, Math.max(5, value))
}

const inputClassName =
  'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-100'
