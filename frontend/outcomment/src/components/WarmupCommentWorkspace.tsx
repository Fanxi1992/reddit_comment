import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { streamWarmupCollection, streamWarmupComments } from '../lib/api'
import { downloadWarmupCommentsCsv, downloadWarmupCommentsXlsx } from '../lib/excel'
import { isValidRedditUrl, normalizeUrl } from '../lib/validation'
import type {
  WarmupCollectedPost,
  WarmupCollectStreamEvent,
  WarmupCollectSummary,
  WarmupCommentResult,
  WarmupCommentStreamEvent,
  WarmupCommentSummary,
} from '../types'
import { CopyIcon, DownloadIcon, PlayIcon, SparkIcon, StopIcon, TrashIcon } from './icons'

export const MAX_WARMUP_COMMENT_COUNT = 40
export const MAX_WARMUP_POST_COUNT = 40

type PostTaskStatus = 'pending' | 'collecting' | 'collected' | 'generating' | 'completed' | 'failed'

type PostTaskState = {
  status: PostTaskStatus
  message?: string
}

export function WarmupCommentWorkspace() {
  const [postUrlsText, setPostUrlsText] = useState('')
  const [commentsPerPost, setCommentsPerPost] = useState('20')
  const [customPrompt, setCustomPrompt] = useState('')
  const [isCollecting, setIsCollecting] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [collectionSummary, setCollectionSummary] = useState<WarmupCollectSummary | null>(null)
  const [generationSummary, setGenerationSummary] = useState<WarmupCommentSummary | null>(null)
  const [collectedPosts, setCollectedPosts] = useState<WarmupCollectedPost[]>([])
  const [results, setResults] = useState<WarmupCommentResult[]>([])
  const [postStates, setPostStates] = useState<Record<number, PostTaskState>>({})
  const [collectedSignature, setCollectedSignature] = useState('')
  const collectionAbortRef = useRef<AbortController | null>(null)
  const generationAbortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      collectionAbortRef.current?.abort()
      generationAbortRef.current?.abort()
    }
  }, [])

  const urlPreview = useMemo(() => parseWarmupPostUrls(postUrlsText), [postUrlsText])
  const normalizedCommentsPerPost = clampCount(commentsPerPost)
  const currentSignature = urlPreview.valid.map(normalizeUrl).join('|')
  const isCollectionCurrent = Boolean(collectedSignature) && collectedSignature === currentSignature
  const isOverPostLimit = urlPreview.valid.length > MAX_WARMUP_POST_COUNT
  const plannedCommentCount = collectedPosts.length * normalizedCommentsPerPost
  const isBusy = isCollecting || isGenerating
  const canCollect = urlPreview.valid.length > 0 && !isOverPostLimit && !isBusy
  const canGenerate =
    collectedPosts.length > 0 && isCollectionCurrent && Boolean(customPrompt.trim()) && !isBusy

  const handleCollect = async () => {
    const controller = new AbortController()
    collectionAbortRef.current = controller
    setIsCollecting(true)
    setError(null)
    setCollectionSummary(null)
    setGenerationSummary(null)
    setCollectedPosts([])
    setResults([])
    setCollectedSignature('')
    setPostStates(
      Object.fromEntries(urlPreview.valid.map((_, index) => [index + 1, { status: 'pending' as const }])),
    )

    try {
      await streamWarmupCollection({
        payload: { postUrls: urlPreview.valid },
        signal: controller.signal,
        onEvent: applyCollectionEvent,
      })
    } catch (exc) {
      if (controller.signal.aborted) {
        setError('帖子读取已停止。')
      } else {
        setError(exc instanceof Error ? exc.message : '批量帖子读取失败')
      }
    } finally {
      setIsCollecting(false)
      collectionAbortRef.current = null
    }
  }

  const applyCollectionEvent = (event: WarmupCollectStreamEvent) => {
    if (event.type === 'post_collecting') {
      updatePostState(event.postIndex, { status: 'collecting', message: event.message })
      return
    }
    if (event.type === 'post_collected') {
      updatePostState(event.postIndex, { status: 'collected', message: event.message })
      setCollectedPosts((current) => mergeCollectedPost(current, event.post))
      return
    }
    if (event.type === 'post_failed') {
      updatePostState(event.postIndex, { status: 'failed', message: event.message })
      return
    }
    if (event.type === 'done') {
      setCollectionSummary(event.summary)
      setCollectedPosts([...event.posts].sort((left, right) => left.postIndex - right.postIndex))
      setCollectedSignature(currentSignature)
      return
    }
    if (event.type === 'error') {
      setError(event.message)
    }
  }

  const handleGenerate = async () => {
    const controller = new AbortController()
    generationAbortRef.current = controller
    setIsGenerating(true)
    setError(null)
    setGenerationSummary(null)
    setResults([])
    setPostStates((current) =>
      ({
        ...current,
        ...Object.fromEntries(
          collectedPosts.map((post) => [
            post.postIndex,
            { ...current[post.postIndex], status: 'collected' as const, message: '等待生成' },
          ]),
        ),
      }),
    )

    try {
      await streamWarmupComments({
        payload: {
          posts: collectedPosts,
          customPrompt: customPrompt.trim(),
          commentsPerPost: normalizedCommentsPerPost,
        },
        signal: controller.signal,
        onEvent: applyGenerationEvent,
      })
    } catch (exc) {
      if (controller.signal.aborted) {
        setError('评论生成已停止；已经返回的评论仍然保留。')
      } else {
        setError(exc instanceof Error ? exc.message : '批量预热评论生成失败')
      }
    } finally {
      setIsGenerating(false)
      generationAbortRef.current = null
    }
  }

  const applyGenerationEvent = (event: WarmupCommentStreamEvent) => {
    if (event.type === 'generation_started') {
      updatePostState(event.postIndex, { status: 'generating', message: event.message })
      return
    }
    if (event.type === 'comment_generated') {
      setResults((current) => mergeCommentResult(current, event.result))
      return
    }
    if (event.type === 'post_completed') {
      updatePostState(event.postIndex, { status: 'completed', message: event.message })
      return
    }
    if (event.type === 'post_failed') {
      updatePostState(event.postIndex, { status: 'failed', message: event.message })
      return
    }
    if (event.type === 'done') {
      setGenerationSummary(event.summary)
      setResults(sortCommentResults(event.results))
      return
    }
    if (event.type === 'error') {
      setError(event.message)
    }
  }

  const updatePostState = (postIndex: number, state: PostTaskState) => {
    setPostStates((current) => ({ ...current, [postIndex]: state }))
  }

  const handleStop = () => {
    collectionAbortRef.current?.abort()
    generationAbortRef.current?.abort()
  }

  const updateComment = (postIndex: number, commentIndex: number, text: string) => {
    setResults((current) =>
      current.map((item) => (item.postIndex === postIndex && item.commentIndex === commentIndex ? { ...item, text } : item)),
    )
  }

  const deleteComment = (postIndex: number, commentIndex: number) => {
    setResults((current) =>
      current.filter((item) => !(item.postIndex === postIndex && item.commentIndex === commentIndex)),
    )
  }

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-4 px-4 py-4 lg:px-6">
      <section className="rounded-lg border border-teal-200 bg-gradient-to-r from-teal-50 to-white px-5 py-4 shadow-sm">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-600 text-white">
            <SparkIcon className="h-5 w-5" />
          </span>
          <div>
            <h2 className="text-lg font-semibold text-slate-950">帖子预热评论</h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">
              批量读取帖子正文、图片和已有评论，共享一份策划提示词，为每个帖子分别生成多条独立顶层评论。
            </p>
            <p className="mt-1 text-xs font-medium text-teal-700">
              帖子上下文彼此隔离；单帖失败不会中断本批次其他任务。
            </p>
          </div>
        </div>
      </section>

      {error ? (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700">
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 1</div>
              <h2 className="mt-1 text-base font-semibold text-slate-950">批量读取 Reddit 帖子</h2>
              <p className="mt-1 text-sm text-slate-500">一行一个帖子 URL，系统校验、去重后读取正文、图片和评论。</p>
            </div>
            <span className="rounded-md bg-slate-100 px-2.5 py-1.5 text-xs font-semibold text-slate-500">最多 40 帖</span>
          </div>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">Reddit 帖子 URL</span>
            <textarea
              aria-label="Reddit 帖子 URL"
              className={`${inputClassName} mt-1 min-h-40 resize-y font-mono leading-6`}
              disabled={isBusy}
              onChange={(event) => setPostUrlsText(event.target.value)}
              placeholder={`https://www.reddit.com/r/example/comments/...\nhttps://www.reddit.com/r/example/comments/...`}
              value={postUrlsText}
            />
          </label>

          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            <PreviewMetric label="输入行数" value={String(urlPreview.rawCount)} />
            <PreviewMetric label="有效去重帖子" value={String(urlPreview.valid.length)} />
            <PreviewMetric label="无效 / 重复" value={String(urlPreview.invalid.length + urlPreview.duplicateCount)} />
          </div>

          {isOverPostLimit ? (
            <Notice tone="error">单批最多处理 {MAX_WARMUP_POST_COUNT} 个有效去重帖子。</Notice>
          ) : null}
          {urlPreview.invalid.length > 0 ? (
            <Notice tone="warning">
              <span className="font-semibold">以下内容不会进入任务：</span>{' '}
              {urlPreview.invalid.slice(0, 5).join('；')}
              {urlPreview.invalid.length > 5 ? `；另有 ${urlPreview.invalid.length - 5} 条` : ''}
            </Notice>
          ) : null}
          {collectedPosts.length > 0 && !isCollectionCurrent ? (
            <Notice tone="warning">URL 已发生变化，请重新读取后再生成评论。</Notice>
          ) : null}

          <button
            className={primaryButtonClassName}
            disabled={!canCollect}
            onClick={() => void handleCollect()}
            type="button"
          >
            <PlayIcon />
            读取 {Math.min(urlPreview.valid.length, MAX_WARMUP_POST_COUNT)} 个帖子
          </button>

          {isCollecting ? (
            <button className={stopButtonClassName} onClick={handleStop} type="button">
              <StopIcon />停止读取
            </button>
          ) : null}

          <CollectionPreview
            posts={collectedPosts}
            states={postStates}
            summary={collectionSummary}
            totalPosts={urlPreview.valid.length}
          />
        </section>

        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 2</div>
            <h2 className="mt-1 text-base font-semibold text-slate-950">设置统一生成要求</h2>
            <p className="mt-1 text-sm text-slate-500">提示词全批次共用，但模型会分别结合每个帖子的独立上下文和图片。</p>
          </div>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">每帖评论数量</span>
            <input
              aria-label="每帖评论数量"
              className={`${inputClassName} mt-1`}
              disabled={isBusy}
              max={MAX_WARMUP_COMMENT_COUNT}
              min={1}
              onBlur={() => setCommentsPerPost(String(normalizedCommentsPerPost))}
              onChange={(event) => setCommentsPerPost(event.target.value)}
              type="number"
              value={commentsPerPost}
            />
            <span className="mt-1 block text-xs text-slate-500">
              每帖最多 {MAX_WARMUP_COMMENT_COUNT} 条；当前成功读取 {collectedPosts.length} 帖，计划生成 {plannedCommentCount} 条。
            </span>
          </label>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">本批次共用提示词</span>
            <textarea
              aria-label="本批次共用提示词"
              className={`${inputClassName} mt-1 min-h-56 resize-y leading-6`}
              disabled={isBusy}
              onChange={(event) => setCustomPrompt(event.target.value)}
              placeholder="描述评论的语言、语气、角度、长短、是否允许玩梗，以及需要避免的内容。"
              value={customPrompt}
            />
          </label>

          <button
            className={primaryButtonClassName}
            disabled={!canGenerate}
            onClick={() => void handleGenerate()}
            type="button"
          >
            <PlayIcon />
            为 {collectedPosts.length} 个帖子生成评论
          </button>
          {isGenerating ? (
            <button className={stopButtonClassName} onClick={handleStop} type="button">
              <StopIcon />停止生成
            </button>
          ) : null}

          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            <PreviewMetric label="已生成评论" value={String(results.length)} />
            <PreviewMetric
              label="完成帖子"
              value={`${generationSummary?.successfulPosts ?? countStatus(postStates, 'completed')} / ${collectedPosts.length}`}
            />
          </div>
        </section>
      </div>

      <ResultWorkspace
        commentsPerPost={normalizedCommentsPerPost}
        posts={collectedPosts}
        postStates={postStates}
        results={results}
        summary={generationSummary}
        onCopy={(text) => void navigator.clipboard.writeText(text)}
        onDelete={deleteComment}
        onExportCsv={() => downloadWarmupCommentsCsv(collectedPosts, results)}
        onExportXlsx={() => downloadWarmupCommentsXlsx(collectedPosts, results)}
        onUpdate={updateComment}
      />
    </div>
  )
}

function CollectionPreview({
  posts,
  states,
  summary,
  totalPosts,
}: {
  posts: WarmupCollectedPost[]
  states: Record<number, PostTaskState>
  summary: WarmupCollectSummary | null
  totalPosts: number
}) {
  if (!posts.length && !Object.keys(states).length) {
    return (
      <div className="mt-4 rounded-md border border-dashed border-slate-300 bg-slate-50 px-5 py-8 text-center">
        <div className="text-sm font-semibold text-slate-700">等待批量读取帖子</div>
        <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500">
          读取后将展示标题、正文摘要、帖子类型、图片数量和已感知评论数量。
        </p>
      </div>
    )
  }

  return (
    <div className="mt-4 space-y-2">
      <div className="flex items-center justify-between text-xs font-semibold text-slate-500">
        <span>帖子感知进度</span>
        <span>{summary?.processedPosts ?? countProcessed(states)} / {totalPosts}</span>
      </div>
      {Object.entries(states)
        .sort(([left], [right]) => Number(left) - Number(right))
        .map(([index, state]) => {
          const post = posts.find((item) => item.postIndex === Number(index))
          return (
            <article className="rounded-md border border-slate-200 p-3" key={index}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-slate-500">帖子 #{index}</div>
                  <div className="mt-1 truncate text-sm font-semibold text-slate-900">
                    {post?.title || state.message || '等待处理'}
                  </div>
                </div>
                <StatusBadge status={state.status} />
              </div>
              {post ? (
                <>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-600">
                    <span>正文 {post.bodyLength} 字符</span>
                    <span>图片 {post.mediaUrls.length}</span>
                    <span>评论 {post.includedCommentCount}</span>
                  </div>
                  {post.bodyText ? <p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-500">{post.bodyText}</p> : null}
                  {post.mediaUrls.length ? (
                    <div className="mt-2 flex gap-2 overflow-x-auto">
                      {post.mediaUrls.slice(0, 3).map((url) => (
                        <img
                          alt={`帖子 ${post.postIndex} 图片`}
                          className="h-16 w-20 shrink-0 rounded border border-slate-200 object-cover"
                          key={url}
                          loading="lazy"
                          referrerPolicy="no-referrer"
                          src={url}
                        />
                      ))}
                    </div>
                  ) : null}
                </>
              ) : state.message ? <p className="mt-2 text-xs text-slate-500">{state.message}</p> : null}
            </article>
          )
        })}
    </div>
  )
}

function ResultWorkspace({
  commentsPerPost,
  posts,
  postStates,
  results,
  summary,
  onCopy,
  onDelete,
  onExportCsv,
  onExportXlsx,
  onUpdate,
}: {
  commentsPerPost: number
  posts: WarmupCollectedPost[]
  postStates: Record<number, PostTaskState>
  results: WarmupCommentResult[]
  summary: WarmupCommentSummary | null
  onCopy: (text: string) => void
  onDelete: (postIndex: number, commentIndex: number) => void
  onExportCsv: () => void
  onExportXlsx: () => void
  onUpdate: (postIndex: number, commentIndex: number, text: string) => void
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 3</div>
          <h2 className="mt-1 text-base font-semibold text-slate-950">按帖子查看评论结果</h2>
          <p className="mt-1 text-sm text-slate-500">每条评论可以单独编辑、复制或删除，也可以导出整个批次。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-600">
            帖子 {summary?.successfulPosts ?? countStatus(postStates, 'completed')} / {posts.length}
          </span>
          <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-600">
            评论 {results.length} / {posts.length * commentsPerPost}
          </span>
          <button className={exportButtonClassName} disabled={!results.length} onClick={onExportCsv} type="button">
            <DownloadIcon />CSV
          </button>
          <button className={exportButtonClassName} disabled={!results.length} onClick={onExportXlsx} type="button">
            <DownloadIcon />XLSX
          </button>
        </div>
      </div>

      {!posts.length ? (
        <div className="mt-4 rounded-md border border-dashed border-slate-300 px-5 py-12 text-center text-sm font-medium text-slate-500">
          完成帖子感知并提交生成要求后，每个帖子的评论组将显示在这里。
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {posts.map((post) => {
            const postResults = results.filter((item) => item.postIndex === post.postIndex)
            const state = postStates[post.postIndex] ?? { status: 'collected' as const }
            return (
              <article className="rounded-lg border border-slate-200 bg-slate-50 p-3" key={post.postIndex}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-semibold text-teal-700">帖子 #{post.postIndex} · {post.subreddit || 'unknown'}</div>
                    <a
                      className="mt-1 block truncate text-sm font-semibold text-slate-950 hover:text-teal-700"
                      href={post.postUrl}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {post.title}
                    </a>
                  </div>
                  <StatusBadge status={state.status} />
                </div>
                {state.message ? <p className="mt-2 text-xs text-slate-500">{state.message}</p> : null}
                {postResults.length ? (
                  <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    {postResults.map((result) => (
                      <div className="rounded-md border border-slate-200 bg-white p-3" key={result.commentIndex}>
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <span className="text-xs font-semibold text-slate-500">评论 #{result.commentIndex}</span>
                          <div className="flex gap-1">
                            <button
                              aria-label={`复制帖子 ${post.postIndex} 评论 ${result.commentIndex}`}
                              className={iconButtonClassName}
                              onClick={() => onCopy(result.text)}
                              title="复制"
                              type="button"
                            >
                              <CopyIcon />
                            </button>
                            <button
                              aria-label={`删除帖子 ${post.postIndex} 评论 ${result.commentIndex}`}
                              className={iconButtonClassName}
                              onClick={() => onDelete(result.postIndex, result.commentIndex)}
                              title="删除"
                              type="button"
                            >
                              <TrashIcon />
                            </button>
                          </div>
                        </div>
                        <textarea
                          aria-label={`帖子 ${post.postIndex} 评论 ${result.commentIndex}`}
                          className={`${inputClassName} min-h-28 resize-y leading-6`}
                          onChange={(event) => onUpdate(result.postIndex, result.commentIndex, event.target.value)}
                          value={result.text}
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="mt-3 rounded-md border border-dashed border-slate-300 bg-white px-3 py-6 text-center text-xs text-slate-500">
                    {state.status === 'failed' ? state.message || '该帖子处理失败' : '等待生成评论'}
                  </div>
                )}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

type WarmupUrlPreview = {
  rawCount: number
  valid: string[]
  invalid: string[]
  duplicateCount: number
}

function parseWarmupPostUrls(rawText: string): WarmupUrlPreview {
  const lines = rawText.split(/\r?\n/).map((value) => value.trim()).filter(Boolean)
  const valid: string[] = []
  const invalid: string[] = []
  const seen = new Set<string>()
  let duplicateCount = 0

  for (const value of lines) {
    if (!isValidRedditPostUrl(value)) {
      invalid.push(value)
      continue
    }
    const normalized = normalizeUrl(value)
    if (seen.has(normalized)) {
      duplicateCount += 1
      continue
    }
    seen.add(normalized)
    valid.push(value)
  }
  return { rawCount: lines.length, valid, invalid, duplicateCount }
}

function isValidRedditPostUrl(value: string): boolean {
  if (!isValidRedditUrl(value)) return false
  try {
    const parsed = new URL(value)
    const host = parsed.hostname.toLowerCase()
    if (host === 'redd.it' || host.endsWith('.redd.it')) return Boolean(parsed.pathname.replaceAll('/', ''))
    return parsed.pathname.toLowerCase().includes('/comments/')
  } catch {
    return false
  }
}

function clampCount(value: string): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 1
  return Math.min(MAX_WARMUP_COMMENT_COUNT, Math.max(1, Math.trunc(parsed)))
}

function mergeCollectedPost(posts: WarmupCollectedPost[], next: WarmupCollectedPost): WarmupCollectedPost[] {
  return [...posts.filter((item) => item.postIndex !== next.postIndex), next].sort((left, right) => left.postIndex - right.postIndex)
}

function mergeCommentResult(results: WarmupCommentResult[], next: WarmupCommentResult): WarmupCommentResult[] {
  return sortCommentResults([
    ...results.filter((item) => !(item.postIndex === next.postIndex && item.commentIndex === next.commentIndex)),
    next,
  ])
}

function sortCommentResults(results: WarmupCommentResult[]): WarmupCommentResult[] {
  return [...results].sort((left, right) => left.postIndex - right.postIndex || left.commentIndex - right.commentIndex)
}

function countStatus(states: Record<number, PostTaskState>, status: PostTaskStatus): number {
  return Object.values(states).filter((state) => state.status === status).length
}

function countProcessed(states: Record<number, PostTaskState>): number {
  return Object.values(states).filter((state) => state.status === 'collected' || state.status === 'failed').length
}

function StatusBadge({ status }: { status: PostTaskStatus }) {
  const labels: Record<PostTaskStatus, string> = {
    pending: '等待',
    collecting: '感知中',
    collected: '已感知',
    generating: '生成中',
    completed: '已完成',
    failed: '失败',
  }
  const colors = status === 'failed'
    ? 'bg-rose-50 text-rose-700'
    : status === 'completed' || status === 'collected'
      ? 'bg-emerald-50 text-emerald-700'
      : status === 'collecting' || status === 'generating'
        ? 'bg-teal-50 text-teal-700'
        : 'bg-slate-100 text-slate-500'
  return <span className={`rounded px-2 py-1 text-xs font-semibold ${colors}`}>{labels[status]}</span>
}

function Notice({ children, tone }: { children: ReactNode; tone: 'warning' | 'error' }) {
  const colors = tone === 'error' ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-amber-200 bg-amber-50 text-amber-800'
  return <div className={`mt-3 rounded-md border px-3 py-2 text-sm ${colors}`}>{children}</div>
}

function PreviewMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div className="text-xs font-semibold text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-950">{value}</div>
    </div>
  )
}

const inputClassName =
  'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-100 disabled:bg-slate-50 disabled:text-slate-500'
const primaryButtonClassName =
  'mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-teal-600 px-4 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300'
const stopButtonClassName =
  'mt-2 inline-flex h-9 w-full items-center justify-center gap-2 rounded-md border border-rose-200 bg-white px-4 text-sm font-semibold text-rose-700 hover:bg-rose-50'
const exportButtonClassName =
  'inline-flex h-9 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-slate-300'
const iconButtonClassName =
  'inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-teal-700'
