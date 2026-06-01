import { useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { generateQueryPlan, streamRedditSearch } from '../lib/api'
import {
  downloadManualUrlExcelTemplate,
  downloadRedditSearchResultsCsv,
  downloadRedditSearchResultsXlsx,
  parseManualUrlExcelFile,
} from '../lib/excel'
import { normalizeUrl } from '../lib/validation'
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
import { CheckIcon, DownloadIcon, PlayIcon, PlusIcon, SparkIcon, StopIcon, TrashIcon, UploadIcon } from './icons'
import { CommentDecisionPanel } from './CommentDecisionPanel'
import { CrawlOnlyPanel } from './CrawlOnlyPanel'

const DEFAULT_CONTEXT: ProductContext = {
  productName: '',
  productDescription: '',
  targetAudience: '',
  sellingPoints: '',
  competitors: '',
  commentRequirements: '',
  forbiddenTopics: '',
  desiredQueryCount: 12,
}

const MAX_CANDIDATE_QUERY_COUNT = 20
const MAX_APPROVED_QUERY_COUNT = 6
const MAX_CRAWL_ONLY_URL_COUNT = 120

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

type UrlSourceMode = 'search' | 'manual' | 'crawl-search' | 'crawl-manual'

export function QueryPlanWorkspace() {
  const [urlSourceMode, setUrlSourceMode] = useState<UrlSourceMode>('search')
  const [context, setContext] = useState<ProductContext>(DEFAULT_CONTEXT)
  const [queries, setQueries] = useState<PlannedQuery[]>([])
  const [crawlQueries, setCrawlQueries] = useState<PlannedQuery[]>([createPlannedQuery(EMPTY_QUERY)])
  const [approvedPlan, setApprovedPlan] = useState<ApprovedQueryPlan | null>(null)
  const [isGenerating, setIsGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [querySearchStates, setQuerySearchStates] = useState<Record<string, QuerySearchState>>({})
  const [searchResults, setSearchResults] = useState<RedditSearchResultItem[]>([])
  const [searchSummary, setSearchSummary] = useState<RedditSearchSummary | null>(null)
  const [isSearching, setIsSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [manualUrlsText, setManualUrlsText] = useState('')
  const [manualUrlError, setManualUrlError] = useState<string | null>(null)
  const searchAbortRef = useRef<AbortController | null>(null)
  const manualUrlFileInputRef = useRef<HTMLInputElement | null>(null)

  const validQueryCount = useMemo(
    () => queries.filter((item) => item.query.trim() && item.reason.trim()).length,
    [queries],
  )
  const validCrawlQueries = useMemo(
    () =>
      crawlQueries
        .map((item) => ({
          ...item,
          query: item.query.trim(),
          reason: item.reason.trim() || 'Manual simulated search query',
        }))
        .filter((item) => item.query)
        .slice(0, MAX_APPROVED_QUERY_COUNT),
    [crawlQueries],
  )
  const canGenerate = context.productName.trim() && context.productDescription.trim() && !isGenerating
  const canApprove = validQueryCount > 0 && validQueryCount <= MAX_APPROVED_QUERY_COUNT && !isGenerating
  const canStartSearch = Boolean(approvedPlan?.queries.length) && !isGenerating && !isSearching
  const manualUrlPreview = useMemo(() => parseManualRedditUrls(manualUrlsText), [manualUrlsText])
  const isCommentSearchMode = urlSourceMode === 'search'
  const isCommentManualMode = urlSourceMode === 'manual'
  const isCrawlSearchMode = urlSourceMode === 'crawl-search'
  const isCrawlManualMode = urlSourceMode === 'crawl-manual'

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
    setManualUrlError(null)
    resetSearchState()
  }

  const changeUrlSourceMode = (mode: UrlSourceMode) => {
    setUrlSourceMode(mode)
    setApprovedPlan(null)
    setError(null)
    setManualUrlError(null)
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

  const updateCrawlQuery = <K extends keyof PlannedQuery>(id: string, key: K, value: PlannedQuery[K]) => {
    setCrawlQueries((current) => current.map((item) => (item.id === id ? { ...item, [key]: value } : item)))
    resetSearchState()
  }

  const addCrawlQuery = () => {
    setCrawlQueries((current) =>
      current.length >= MAX_APPROVED_QUERY_COUNT ? current : [...current, createPlannedQuery(EMPTY_QUERY)],
    )
    resetSearchState()
  }

  const removeCrawlQuery = (id: string) => {
    setCrawlQueries((current) => {
      const next = current.filter((item) => item.id !== id)
      return next.length ? next : [createPlannedQuery(EMPTY_QUERY)]
    })
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

    if (approvedQueries.length > MAX_APPROVED_QUERY_COUNT) {
      setError(`最多只能批准 ${MAX_APPROVED_QUERY_COUNT} 条 Query 进入 Reddit 搜索，请删除或清空多余候选项。`)
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

  const prepareManualUrls = () => {
    if (isCommentManualMode && (!context.productName.trim() || !context.productDescription.trim())) {
      setManualUrlError('产品名称和产品情况是必填项。')
      return
    }
    if (!manualUrlPreview.valid.length) {
      setManualUrlError('请粘贴至少一个有效的 Reddit 帖子 URL。')
      return
    }
    if ((isCommentManualMode || isCrawlManualMode) && manualUrlPreview.valid.length > MAX_CRAWL_ONLY_URL_COUNT) {
      setManualUrlError(`手动导入 URL 最多支持 ${MAX_CRAWL_ONLY_URL_COUNT} 条有效去重 URL，请删减后再开始。`)
      return
    }

    const manualQuery = createPlannedQuery({
      query: 'manual_url_upload',
      intent: 'other',
      reason: 'Manual Reddit URL upload',
      priority: 1,
      suggestedTimeRange: 'all',
    })
    const manualResults = manualUrlPreview.valid.map((url, index) => buildManualSearchResult(url, index + 1))
    setApprovedPlan(
      isCommentManualMode
        ? {
            productContext: { ...context, desiredQueryCount: 1 },
            queries: [manualQuery],
            approvedAt: new Date().toISOString(),
          }
        : null,
    )
    setSearchResults(manualResults)
    setSearchSummary({
      totalQueries: 1,
      successfulQueries: 1,
      failedQueries: 0,
      rawUrlCount: manualUrlPreview.rawCount,
      uniqueUrlCount: manualResults.length,
    })
    setQuerySearchStates({})
    setManualUrlError(null)
    setSearchError(null)
    setError(null)
  }

  const handleManualUrlExcelUpload = async (file: File | undefined) => {
    if (!file) {
      return
    }

    setManualUrlError(null)
    try {
      const urls = await parseManualUrlExcelFile(file)
      if (!urls.length) {
        setManualUrlError('Excel 里没有读取到 URL。')
        return
      }
      setManualUrlsText((current) => [current.trim(), urls.join('\n')].filter(Boolean).join('\n'))
      setApprovedPlan(null)
      setSearchResults([])
      setSearchSummary(null)
    } catch (exc) {
      setManualUrlError(exc instanceof Error ? exc.message : 'Excel 解析失败')
    } finally {
      if (manualUrlFileInputRef.current) {
        manualUrlFileInputRef.current.value = ''
      }
    }
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
    <div className="mx-auto grid w-full max-w-[1600px] gap-4 px-4 py-3 lg:grid-cols-[400px_minmax(0,1fr)] lg:px-6">
      <aside className="min-w-0 space-y-2.5 rounded-md border border-slate-200 bg-white p-3 shadow-sm lg:sticky lg:top-3 lg:max-h-[calc(100vh-104px)] lg:overflow-y-auto">
        <div>
          <h2 className="text-base font-semibold text-slate-950">产品上下文</h2>
          <p className="mt-1 text-sm text-slate-500">
            {isCrawlSearchMode || isCrawlManualMode ? '仅评论生成模式使用；仅抓取模式不要求填写。' : '用于搜索裂解和评论生成提示词。'}
          </p>
        </div>

        <Field label="产品名称" required={!isCrawlSearchMode && !isCrawlManualMode}>
          <input
            className={inputClassName}
            onChange={(event) => updateContext('productName', event.target.value)}
            placeholder="例如：AI video editor"
            value={context.productName}
          />
        </Field>

        <Field label="产品情况" required={!isCrawlSearchMode && !isCrawlManualMode}>
          <textarea
            className={`${inputClassName} min-h-20 resize-y leading-6`}
            onChange={(event) => updateContext('productDescription', event.target.value)}
            placeholder="产品解决什么问题、核心功能、使用场景、价格/地区等"
            value={context.productDescription}
          />
        </Field>

        <Field label="目标用户">
          <textarea
            className={`${inputClassName} min-h-16 resize-y leading-6`}
            onChange={(event) => updateContext('targetAudience', event.target.value)}
            placeholder="用户画像、行业、痛点、使用动机"
            value={context.targetAudience}
          />
        </Field>

        <Field label="卖点">
          <textarea
            className={`${inputClassName} min-h-16 resize-y leading-6`}
            onChange={(event) => updateContext('sellingPoints', event.target.value)}
            placeholder="希望自然带出的优势"
            value={context.sellingPoints}
          />
        </Field>

        <Field label="竞品">
          <textarea
            className={`${inputClassName} min-h-14 resize-y leading-6`}
            onChange={(event) => updateContext('competitors', event.target.value)}
            placeholder="竞品、替代方案、常被比较的品牌"
            value={context.competitors}
          />
        </Field>

        <Field label="评论要求">
          <textarea
            className={`${inputClassName} min-h-[72px] resize-y leading-6`}
            onChange={(event) => updateContext('commentRequirements', event.target.value)}
            placeholder="语气、长度、是否允许提品牌、是否需要移动端口吻"
            value={context.commentRequirements}
          />
        </Field>

        <Field label="禁忌点">
          <textarea
            className={`${inputClassName} min-h-14 resize-y leading-6`}
            onChange={(event) => updateContext('forbiddenTopics', event.target.value)}
            placeholder="不要提及的卖点、地区、价格、竞品或表达方式"
            value={context.forbiddenTopics}
          />
        </Field>

        {isCommentSearchMode && (
          <>
            <Field label="候选 Query 数量">
              <input
                className={inputClassName}
                max={MAX_CANDIDATE_QUERY_COUNT}
                min={1}
                onChange={(event) =>
                  updateContext('desiredQueryCount', clampQueryCount(Number(event.target.value) || 12))
                }
                type="number"
                value={context.desiredQueryCount}
              />
            </Field>

            <div className="sticky bottom-0 -mx-3 -mb-3 border-t border-slate-200 bg-white px-3 py-2.5">
              <button
                className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-teal-600 px-4 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                disabled={!canGenerate}
                onClick={() => void handleGenerate()}
                type="button"
              >
                <SparkIcon />
                {isGenerating ? '生成中...' : '生成搜索 Query'}
              </button>
            </div>
          </>
        )}
      </aside>

      <section className="min-w-0 space-y-4">
        <div className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-slate-950">URL 来源</h2>
              <p className="mt-1 text-sm text-slate-500">选择 URL 获取方式和后续处理目标。</p>
            </div>
            <div className="grid grid-cols-2 rounded-md bg-slate-100 p-0.5 xl:grid-cols-4">
              {[
                { value: 'search', label: 'AI 裂解搜索' },
                { value: 'manual', label: '手动导入 URL' },
                { value: 'crawl-search', label: '模拟搜索（仅抓取）' },
                { value: 'crawl-manual', label: '手动导入 URL（仅抓取）' },
              ].map((option) => (
                <button
                  className={`h-9 rounded-md px-3 text-sm font-semibold transition ${
                    urlSourceMode === option.value ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-900'
                  }`}
                  key={option.value}
                  onClick={() => changeUrlSourceMode(option.value as UrlSourceMode)}
                  type="button"
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {isCommentSearchMode ? (
          <div className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">Query 审核列表</h2>
                <p className="mt-1 text-sm text-slate-500">
                  生成后可人工编辑、删除、补充和批准；最多批准 {MAX_APPROVED_QUERY_COUNT} 条进入 Reddit 搜索。
                </p>
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

            {validQueryCount > MAX_APPROVED_QUERY_COUNT && (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800">
                当前有效 Query 为 {validQueryCount} 条。请筛选到最多 {MAX_APPROVED_QUERY_COUNT} 条后再批准执行搜索。
              </div>
            )}

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
        ) : null}

        {isCrawlSearchMode ? (
          <section className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">模拟搜索 Query</h2>
                <p className="mt-1 text-sm text-slate-500">人工提供最多 {MAX_APPROVED_QUERY_COUNT} 条搜索 Query，系统会模拟 Reddit 搜索并进入语料仅抓取。</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-700">
                  有效 {validCrawlQueries.length} / 全部 {crawlQueries.length}
                </span>
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-slate-300"
                  disabled={crawlQueries.length >= MAX_APPROVED_QUERY_COUNT}
                  onClick={addCrawlQuery}
                  type="button"
                >
                  <PlusIcon />
                  新增
                </button>
              </div>
            </div>

            <div className="mt-3 grid gap-3">
              {crawlQueries.map((item, index) => (
                <article className="rounded-md border border-slate-200 p-3" key={item.id}>
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-slate-500">#{index + 1}</span>
                    <button
                      aria-label="删除 Query"
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition hover:bg-rose-50 hover:text-rose-600"
                      onClick={() => removeCrawlQuery(item.id)}
                      title="删除 Query"
                      type="button"
                    >
                      <TrashIcon />
                    </button>
                  </div>
                  <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_150px_120px]">
                    <label className="block min-w-0">
                      <span className="text-xs font-semibold text-slate-500">搜索 Query</span>
                      <input
                        className={`${inputClassName} mt-1`}
                        onChange={(event) => updateCrawlQuery(item.id, 'query', event.target.value)}
                        placeholder="例如：best AI video editor"
                        value={item.query}
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-semibold text-slate-500">时间范围</span>
                      <select
                        className={`${inputClassName} mt-1`}
                        onChange={(event) =>
                          updateCrawlQuery(item.id, 'suggestedTimeRange', event.target.value as SuggestedTimeRange)
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
                        onChange={(event) => updateCrawlQuery(item.id, 'priority', Number(event.target.value))}
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
                </article>
              ))}
            </div>
          </section>
        ) : null}

        {isCommentManualMode || isCrawlManualMode ? (
          <section className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">手动导入 Reddit URL</h2>
                <p className="mt-1 text-sm text-slate-500">
                  {isCrawlManualMode
                    ? '一行一个 Reddit 帖子 URL，系统会校验、去重，然后进入语料仅抓取。'
                    : '一行一个 Reddit 帖子 URL，系统会校验、去重，然后直接进入评论决策。'}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700"
                  onClick={() => manualUrlFileInputRef.current?.click()}
                  type="button"
                >
                  <UploadIcon />
                  上传 XLSX
                </button>
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700"
                  onClick={downloadManualUrlExcelTemplate}
                  type="button"
                >
                  <DownloadIcon />
                  下载模板
                </button>
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-md bg-teal-600 px-3 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                  disabled={!manualUrlPreview.valid.length || manualUrlPreview.valid.length > MAX_CRAWL_ONLY_URL_COUNT}
                  onClick={prepareManualUrls}
                  type="button"
                >
                  <CheckIcon />
                  {isCrawlManualMode ? '准备仅抓取' : '准备评论决策'}
                </button>
              </div>
            </div>

            <input
              accept=".xlsx"
              className="hidden"
              onChange={(event) => void handleManualUrlExcelUpload(event.target.files?.[0])}
              ref={manualUrlFileInputRef}
              type="file"
            />

            <textarea
              className={`${inputClassName} mt-3 min-h-56 resize-y font-mono leading-6`}
              onChange={(event) => {
                setManualUrlsText(event.target.value)
                setManualUrlError(null)
                setApprovedPlan(null)
                setSearchResults([])
                setSearchSummary(null)
              }}
              placeholder={`https://www.reddit.com/r/example/comments/...\nhttps://www.reddit.com/r/example/comments/...`}
              value={manualUrlsText}
            />

            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <Metric label="输入行数" value={manualUrlPreview.rawCount} />
              <Metric label="有效去重 URL" value={manualUrlPreview.valid.length} />
              <Metric label="无效/重复" value={manualUrlPreview.invalid.length + manualUrlPreview.duplicateCount} />
            </div>

            {(isCommentManualMode || isCrawlManualMode) && manualUrlPreview.valid.length > MAX_CRAWL_ONLY_URL_COUNT ? (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800">
                当前有效去重 URL 为 {manualUrlPreview.valid.length} 条，手动导入 URL 最多支持 {MAX_CRAWL_ONLY_URL_COUNT} 条。
              </div>
            ) : null}

            {manualUrlError && (
              <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">
                {manualUrlError}
              </div>
            )}

            {manualUrlPreview.invalid.length > 0 && (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                <div className="font-semibold">以下 URL 会被忽略：</div>
                <div className="mt-1 max-h-28 overflow-y-auto font-mono text-xs">
                  {manualUrlPreview.invalid.slice(0, 12).map((url) => (
                    <div className="truncate" key={url} title={url}>
                      {url}
                    </div>
                  ))}
                  {manualUrlPreview.invalid.length > 12 ? <div>还有 {manualUrlPreview.invalid.length - 12} 条...</div> : null}
                </div>
              </div>
            )}

            {searchResults.length > 0 && (
              <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700">
                已准备 {searchResults.length} 条去重 URL，可以{isCrawlManualMode ? '开始语料仅抓取' : '生成评论决策'}。
              </div>
            )}
          </section>
        ) : null}

        {isCommentSearchMode && approvedPlan && (
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

        {isCrawlSearchMode && validCrawlQueries.length > 0 ? (
          <CrawlOnlyPanel key={`crawl-search-${validCrawlQueries.map((item) => item.query).join('|')}`} queries={validCrawlQueries} source="simulated_search" />
        ) : null}

        {isCrawlManualMode && searchResults.length > 0 ? (
          <CrawlOnlyPanel key={`crawl-manual-${searchResults.map((item) => item.postUrl).join('|')}`} searchResults={searchResults} source="manual_urls" />
        ) : null}

        {(isCommentSearchMode || isCommentManualMode) && approvedPlan && searchResults.length > 0 && (
          <CommentDecisionPanel
            approvedPlan={approvedPlan}
            key={searchResults.map((item) => item.postUrl).join('|')}
            searchResults={searchResults}
          />
        )}

        {isCommentSearchMode && queries.length === 0 ? (
          <div className="rounded-md border border-dashed border-slate-300 bg-white px-5 py-12 text-center text-sm font-medium text-slate-500">
            请输入产品上下文并生成 Query
          </div>
        ) : null}

        {isCommentSearchMode && queries.length > 0 ? (
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
        ) : null}
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

function parseManualRedditUrls(rawText: string): {
  rawCount: number
  valid: string[]
  invalid: string[]
  duplicateCount: number
} {
  const lines = rawText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const seen = new Set<string>()
  const valid: string[] = []
  const invalid: string[] = []
  let duplicateCount = 0

  for (const line of lines) {
    const normalized = normalizeManualRedditPostUrl(line)
    if (!normalized) {
      invalid.push(line)
      continue
    }
    const key = normalizeUrl(normalized)
    if (seen.has(key)) {
      duplicateCount += 1
      continue
    }
    seen.add(key)
    valid.push(normalized)
  }

  return {
    rawCount: lines.length,
    valid,
    invalid,
    duplicateCount,
  }
}

function normalizeManualRedditPostUrl(value: string): string {
  try {
    const parsed = new URL(value.trim())
    const host = parsed.hostname.toLowerCase()
    if (host !== 'reddit.com' && host !== 'www.reddit.com' && !host.endsWith('.reddit.com')) {
      return ''
    }
    const normalizedPath = parsed.pathname.replace(/\/+/g, '/').replace(/\/$/, '')
    const match = normalizedPath.match(/^(\/r\/[^/]+\/comments\/[^/]+(?:\/[^/]+)?)/i)
    if (!match) {
      return ''
    }
    return `https://www.reddit.com${match[1]}`
  } catch {
    return ''
  }
}

function buildManualSearchResult(postUrl: string, resultIndex: number): RedditSearchResultItem {
  return {
    query: 'manual_url_upload',
    queryIntent: 'other',
    priority: 1,
    timeRange: 'all',
    resultIndex,
    postUrl,
    postId: extractPostId(postUrl),
    title: `Manual Reddit URL #${resultIndex}`,
    subreddit: extractSubreddit(postUrl) || 'unknown',
    ageText: '',
    votes: null,
    comments: null,
    duplicateOfQuery: null,
    matchedQueries: ['manual_url_upload'],
  }
}

function extractPostId(url: string): string {
  return url.match(/\/comments\/([^/?#]+)/i)?.[1] ?? ''
}

function extractSubreddit(url: string): string {
  const subreddit = url.match(/\/r\/([^/]+)/i)?.[1]
  return subreddit ? `r/${subreddit}` : ''
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
  return Math.min(MAX_CANDIDATE_QUERY_COUNT, Math.max(1, value))
}

const inputClassName =
  'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-100'
