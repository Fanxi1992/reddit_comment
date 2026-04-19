import { useEffect, useMemo, useRef, useState } from 'react'

import { AnalysisForm } from './components/AnalysisForm'
import { ExcelImportPanel } from './components/ExcelImportPanel'
import { ManualPostEditor } from './components/ManualPostEditor'
import { PostInputTabs } from './components/PostInputTabs'
import { PostPreviewTable } from './components/PostPreviewTable'
import { ProgressPanel } from './components/ProgressPanel'
import { ResultCard } from './components/ResultCard'
import { streamAnalysis } from './lib/api'
import { createPostInput, getSubmittablePosts, normalizeUrl, validatePosts } from './lib/validation'
import type { AnalysisResult, PostInput, ResultItem, StreamEvent, StreamSummary, TaskStage } from './types'

const DEFAULT_PROMPT = '请分析这些 Reddit 帖子的用户痛点、讨论焦点、潜在营销切入点，并给出可执行的内容建议。'

type BackendStatus = 'checking' | 'online' | 'offline'
type InputTab = 'manual' | 'excel'

export default function App() {
  const [posts, setPosts] = useState<PostInput[]>(() => validatePosts([createPostInput('manual')]))
  const [activeTab, setActiveTab] = useState<InputTab>('manual')
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT)
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

  useEffect(() => {
    let isMounted = true

    fetch('/api/health')
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
        setStage('idle')
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
      setResults((current) => markFirstQueuedAsProcessing(current))
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
            <h1 className="text-xl font-semibold tracking-tight text-slate-950">Reddit 洞察分析台</h1>
            <p className="mt-1 text-sm text-slate-500">批量帖子解析 · Gemini 多模态分析 · 实时结果流</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill status={backendStatus} />
            <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-700">
              {submittablePosts.length} 条可提交
            </span>
          </div>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-[1600px] gap-4 px-4 py-4 lg:grid-cols-[360px_minmax(0,1fr)] lg:px-6">
        <aside className="min-w-0 space-y-4 rounded-md border border-slate-200 bg-white p-4 shadow-sm lg:sticky lg:top-4 lg:max-h-[calc(100vh-32px)] lg:overflow-y-auto">
          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-slate-950">帖子来源</h2>
              <span className="text-xs font-semibold text-slate-500">
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

          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-slate-950">分析结果</h2>
              <span className="text-xs font-semibold text-slate-500">{results.length} 张卡片</span>
            </div>

            {results.length === 0 ? (
              <div className="rounded-md border border-dashed border-slate-300 bg-white px-5 py-10 text-center text-sm font-medium text-slate-500">
                等待任务提交
              </div>
            ) : (
              <div className="grid gap-4">
                {results.map((result) => (
                  <ResultCard item={result} key={result.id} />
                ))}
              </div>
            )}
          </section>
        </section>
      </div>
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

function markFirstQueuedAsProcessing(results: ResultItem[]): ResultItem[] {
  let marked = false

  return results.map((result) => {
    if (!marked && result.status === 'queued') {
      marked = true
      return { ...result, status: 'processing' }
    }
    return result
  })
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
