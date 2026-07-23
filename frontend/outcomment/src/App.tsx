import { useEffect, useMemo, useRef, useState } from 'react'

import { AnalysisForm } from './components/AnalysisForm'
import { ExcelImportPanel } from './components/ExcelImportPanel'
import { ManualPostEditor } from './components/ManualPostEditor'
import { PostInputTabs } from './components/PostInputTabs'
import { PostPreviewTable } from './components/PostPreviewTable'
import { ProgressPanel } from './components/ProgressPanel'
import { QueryPlanWorkspace } from './components/QueryPlanWorkspace'
import { ResultCard } from './components/ResultCard'
import { streamAnalysis } from './lib/api'
import { downloadAnalysisResults } from './lib/excel'
import { createPostInput, getSubmittablePosts, normalizeUrl, validatePosts } from './lib/validation'
import type { AnalysisResult, PostInput, ResultItem, StreamEvent, StreamSummary, TaskStage } from './types'
import { DownloadIcon } from './components/icons'

const MAX_BATCH_POSTS = 50
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

type BackendStatus = 'checking' | 'online' | 'offline'
type InputTab = 'manual' | 'excel'
type AppMode = 'url-analysis' | 'query-plan'

export default function App() {
  const [appMode] = useState<AppMode>('query-plan')
  const [posts, setPosts] = useState<PostInput[]>(() => validatePosts([createPostInput('manual')]))
  const [activeTab, setActiveTab] = useState<InputTab>('manual')
  const [prompt, setPrompt] = useState('')
  const [stage, setStage] = useState<TaskStage>('idle')
  const [message, setMessage] = useState('准备就绪')
  const [summary, setSummary] = useState<StreamSummary | null>(null)
  const [globalError, setGlobalError] = useState<string | null>(null)
  const [results, setResults] = useState<ResultItem[]>([])
  const [backendStatus, setBackendStatus] = useState<BackendStatus>('checking')
  const abortRef = useRef<AbortController | null>(null)

  const validatedPosts = useMemo(() => validatePosts(posts), [posts])
  const submittablePosts = useMemo(() => getSubmittablePosts(validatedPosts), [validatedPosts])
  const completedCount = results.filter((result) => ['success', 'skipped', 'failed'].includes(result.status)).length
  const isRunning = stage === 'crawling' || stage === 'analyzing'
  const isOverBatchLimit = submittablePosts.length > MAX_BATCH_POSTS

  useEffect(() => {
    let isMounted = true

    fetch(`${API_BASE_URL}/api/health`)
      .then((response) => {
        if (isMounted) {
          setBackendStatus(response.ok ? 'online' : 'offline')
        }
      })
      .catch(() => {
        if (isMounted) {
          setBackendStatus('offline')
        }
      })

    return () => {
      isMounted = false
    }
  }, [])

  const replacePosts = (nextPosts: PostInput[]) => {
    setPosts(validatePosts(nextPosts))
  }

  const handleSubmit = async () => {
    const validPosts = getSubmittablePosts(posts)
    if (!validPosts.length || !prompt.trim()) {
      return
    }

    if (validPosts.length > MAX_BATCH_POSTS) {
      setStage('failed')
      setGlobalError(`单批最多支持 ${MAX_BATCH_POSTS} 条有效 Reddit 帖子链接，请减少或去重后再提交`)
      setMessage('超过单批上限')
      return
    }

    const controller = new AbortController()
    abortRef.current = controller
    setStage('crawling')
    setSummary(null)
    setGlobalError(null)
    setMessage('正在启动 Apify 批量爬取')
    setResults(
      validPosts.map((post) => ({
        id: post.id,
        input: post,
        status: 'queued',
      })),
    )

    try {
      await streamAnalysis({
        posts: validPosts,
        customPrompt: prompt.trim(),
        signal: controller.signal,
        onEvent: (event) => handleStreamEvent(event, validPosts),
      })
    } catch (exc) {
      if (controller.signal.aborted) {
        setMessage('任务已停止')
        setStage('cancelled')
        setResults((current) =>
          current.map((item) =>
            item.status === 'queued' || item.status === 'processing'
              ? { ...item, status: 'skipped', reason: '任务已停止，未继续处理' }
              : item,
          ),
        )
        return
      }

      setStage('failed')
      setGlobalError(exc instanceof Error ? exc.message : '请求失败')
      setMessage('请求失败')
    } finally {
      abortRef.current = null
    }
  }

  const handleCancel = () => {
    abortRef.current?.abort()
  }

  const handleStreamEvent = (event: StreamEvent, submittedPosts: PostInput[]) => {
    if (event.type === 'crawl_started') {
      setStage('crawling')
      setMessage(event.message ?? '正在启动 Apify 批量爬取')
      return
    }

    if (event.type === 'crawl_completed') {
      setStage('analyzing')
      setMessage(event.message ?? '爬取完成，开始逐个分析')
      return
    }

    if (event.type === 'post_started') {
      setStage('analyzing')
      setMessage(event.message ?? '正在处理帖子')
      setResults((current) => markPostAsProcessing(current, event.inputUrl))
      return
    }

    if (event.type === 'post_result') {
      setStage('analyzing')
      setResults((current) => mergeResult(current, event.result, submittedPosts))
      return
    }

    if (event.type === 'done') {
      setStage('completed')
      setMessage(event.message ?? '分析完成')
      setSummary(event.summary ?? null)
      return
    }

    if (event.type === 'error') {
      setStage('failed')
      setMessage('后端处理失败')
      setGlobalError(event.message ?? '后端处理失败')
    }
  }

  return (
    <main className="min-h-screen overflow-x-hidden bg-slate-100 text-slate-950">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center justify-between gap-3 px-4 py-3 lg:px-6">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-950">Reddit评论智能生成系统</h1>
            <p className="mt-1 text-sm text-slate-500">
              {appMode === 'url-analysis'
                ? '批量帖子解析 · Gemini 多模态分析 · 实时结果流'
                : '产品上下文 · Reddit 搜索 Query 裂解 · 人工审核'}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill status={backendStatus} />
            {appMode === 'url-analysis' ? (
              <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-700">
                {submittablePosts.length} 条可提交
              </span>
            ) : null}
            {appMode === 'url-analysis' && isOverBatchLimit && (
              <span className="rounded-md bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-700">
                超过单批上限 {MAX_BATCH_POSTS} 条
              </span>
            )}
          </div>
        </div>
      </header>

      {appMode === 'query-plan' ? (
        <QueryPlanWorkspace />
      ) : (
        <div className="mx-auto grid w-full max-w-[1600px] gap-4 px-4 py-4 lg:grid-cols-[340px_minmax(0,1fr)] lg:px-6">
          <aside className="min-w-0 space-y-3 rounded-md border border-slate-200 bg-white p-3 shadow-sm lg:sticky lg:top-4 lg:max-h-[calc(100vh-32px)] lg:overflow-y-auto">
            <section className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-slate-950">帖子来源</h2>
                <span className="text-[11px] font-semibold text-slate-500">
                  有效 {submittablePosts.length} / 全部 {validatedPosts.length}
                </span>
              </div>
              <PostInputTabs activeTab={activeTab} onChange={setActiveTab} />
              {activeTab === 'manual' ? (
                <ManualPostEditor posts={validatedPosts} onChange={replacePosts} />
              ) : (
                <ExcelImportPanel onImport={replacePosts} />
              )}
            </section>
          </aside>

          <section className="min-w-0 space-y-4">
            <AnalysisForm
              isRunning={isRunning}
              onCancel={handleCancel}
              onPromptChange={setPrompt}
              onSubmit={() => void handleSubmit()}
              prompt={prompt}
              validCount={submittablePosts.length}
              maxBatchPosts={MAX_BATCH_POSTS}
            />
            <PostPreviewTable posts={validatedPosts} />
            <ProgressPanel
              completedCount={completedCount}
              error={globalError}
              message={message}
              stage={stage}
              summary={summary}
              validCount={submittablePosts.length}
            />

            <section className="min-w-0 space-y-3 overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-base font-semibold text-slate-950">分析结果</h2>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-slate-500">{results.length} 张卡片</span>
                  <button
                    className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-600 transition hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
                    disabled={stage !== 'completed' || results.length === 0}
                    onClick={() => downloadAnalysisResults(results)}
                    type="button"
                  >
                    <DownloadIcon className="h-3.5 w-3.5" />
                    导出结果
                  </button>
                </div>
              </div>

              {results.length === 0 ? (
                <div className="rounded-md border border-dashed border-slate-300 bg-white px-5 py-10 text-center text-sm font-medium text-slate-500">
                  等待任务提交
                </div>
              ) : (
                <div className="grid min-w-0 gap-3 overflow-hidden">
                  {results.map((result) => (
                    <ResultCard item={result} key={result.id} />
                  ))}
                </div>
              )}
            </section>
          </section>
        </div>
      )}
    </main>
  )
}

function StatusPill({ status }: { status: BackendStatus }) {
  const styles = {
    checking: 'bg-slate-100 text-slate-600',
    online: 'bg-emerald-50 text-emerald-700',
    offline: 'bg-rose-50 text-rose-700',
  }

  const labels = {
    checking: '后端检查中',
    online: '后端在线',
    offline: '后端离线',
  }

  return <span className={`rounded-md px-3 py-2 text-sm font-semibold ${styles[status]}`}>{labels[status]}</span>
}

function markPostAsProcessing(results: ResultItem[], inputUrl?: string): ResultItem[] {
  if (!inputUrl) {
    return results
  }

  const normalizedInputUrl = normalizeUrl(inputUrl)
  return results.map((result) =>
    normalizeUrl(result.input.url) === normalizedInputUrl ? { ...result, status: 'processing' } : result,
  )
}

function mergeResult(current: ResultItem[], result: AnalysisResult, submittedPosts: PostInput[]): ResultItem[] {
  const targetUrl = normalizeUrl(result.inputUrl || result.url || '')
  const matchedInput = submittedPosts.find((post) => normalizeUrl(post.url) === targetUrl)

  const applyResult = (item: ResultItem): ResultItem => ({
    ...item,
    status: result.status,
    backendTitle: result.title,
    communityName: result.communityName,
    parsedCommunityName: result.parsedCommunityName,
    reason: result.reason,
    textPreview: result.textPreview,
    imageCount: result.imageCount,
    analysis: result.analysis,
  })

  if (matchedInput) {
    return current.map((item) => (item.input.id === matchedInput.id ? applyResult(item) : item))
  }

  const fallbackInput = createPostInput('manual', {
    url: result.inputUrl || result.url || '',
    validationStatus: 'valid',
  })

  return [
    ...current,
    applyResult({
      id: fallbackInput.id,
      input: fallbackInput,
      status: result.status,
    }),
  ]
}
