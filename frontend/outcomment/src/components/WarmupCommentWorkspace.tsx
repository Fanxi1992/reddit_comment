import { useMemo, useState } from 'react'

import { isValidRedditUrl, normalizeUrl } from '../lib/validation'
import { PlayIcon, SparkIcon } from './icons'

export const MAX_WARMUP_COMMENT_COUNT = 40
export const MAX_WARMUP_POST_COUNT = 40

export function WarmupCommentWorkspace() {
  const [postUrlsText, setPostUrlsText] = useState('')
  const [commentsPerPost, setCommentsPerPost] = useState('20')
  const [customPrompt, setCustomPrompt] = useState('')
  const urlPreview = useMemo(() => parseWarmupPostUrls(postUrlsText), [postUrlsText])
  const normalizedCommentsPerPost = clampCount(commentsPerPost)
  const plannedCommentCount = urlPreview.valid.length * normalizedCommentsPerPost
  const isOverPostLimit = urlPreview.valid.length > MAX_WARMUP_POST_COUNT

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
              批量感知策划人员已经发布的 Reddit 帖子，共享一份自定义提示词，为每个帖子生成多条相互独立的顶层评论。
            </p>
            <p className="mt-1 text-xs font-medium text-teal-700">
              当前为第一阶段页面骨架；批量帖子读取和评论生成将在下一阶段接入。
            </p>
          </div>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 1</div>
              <h2 className="mt-1 text-base font-semibold text-slate-950">批量读取 Reddit 帖子</h2>
              <p className="mt-1 text-sm text-slate-500">一行一个帖子 URL，系统会校验并去重后统一处理。</p>
            </div>
            <span className="rounded-md bg-slate-100 px-2.5 py-1.5 text-xs font-semibold text-slate-500">批量任务</span>
          </div>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">Reddit 帖子 URL</span>
            <textarea
              className={`${inputClassName} mt-1 min-h-40 resize-y font-mono leading-6`}
              onChange={(event) => setPostUrlsText(event.target.value)}
              placeholder={`https://www.reddit.com/r/example/comments/...\nhttps://www.reddit.com/r/example/comments/...`}
              value={postUrlsText}
            />
          </label>

          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            <PreviewMetric label="输入行数" value={String(urlPreview.rawCount)} />
            <PreviewMetric label="有效去重帖子" value={String(urlPreview.valid.length)} />
            <PreviewMetric
              label="无效 / 重复"
              value={String(urlPreview.invalid.length + urlPreview.duplicateCount)}
            />
          </div>

          {isOverPostLimit ? (
            <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">
              单批最多处理 {MAX_WARMUP_POST_COUNT} 个有效去重帖子，当前为 {urlPreview.valid.length} 个。
            </div>
          ) : null}

          {urlPreview.invalid.length > 0 ? (
            <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <div className="font-semibold">以下内容不会进入任务：</div>
              <div className="mt-1 max-h-24 overflow-y-auto font-mono text-xs">
                {urlPreview.invalid.slice(0, 8).map((value) => (
                  <div className="truncate" key={value} title={value}>
                    {value}
                  </div>
                ))}
                {urlPreview.invalid.length > 8 ? <div>还有 {urlPreview.invalid.length - 8} 条...</div> : null}
              </div>
            </div>
          ) : null}

          <button
            className="mt-4 inline-flex h-10 w-full items-center justify-center rounded-md bg-slate-300 px-4 text-sm font-semibold text-white"
            disabled
            title="批量帖子感知接口将在阶段 2 接入"
            type="button"
          >
            读取 {Math.min(urlPreview.valid.length, MAX_WARMUP_POST_COUNT)} 个帖子
          </button>

          <div className="mt-4 rounded-md border border-dashed border-slate-300 bg-slate-50 px-5 py-8 text-center">
            <div className="text-sm font-semibold text-slate-700">等待批量读取帖子</div>
            <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500">
              读取后将按帖子展示标题、正文摘要、帖子类型、图片数量和已感知评论数量，并标明每个帖子的处理状态。
            </p>
          </div>
        </section>

        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 2</div>
            <h2 className="mt-1 text-base font-semibold text-slate-950">设置统一生成要求</h2>
            <p className="mt-1 text-sm text-slate-500">同一批帖子共享提示词，每个帖子分别生成指定数量的顶层评论。</p>
          </div>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">每帖评论数量</span>
            <input
              className={`${inputClassName} mt-1`}
              max={MAX_WARMUP_COMMENT_COUNT}
              min={1}
              onBlur={() => setCommentsPerPost(String(normalizedCommentsPerPost))}
              onChange={(event) => setCommentsPerPost(event.target.value)}
              type="number"
              value={commentsPerPost}
            />
            <span className="mt-1 block text-xs text-slate-500">
              每帖最多 {MAX_WARMUP_COMMENT_COUNT} 条；当前批次计划生成 {plannedCommentCount} 条。
            </span>
          </label>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">本批次共用提示词</span>
            <textarea
              className={`${inputClassName} mt-1 min-h-56 resize-y leading-6`}
              onChange={(event) => setCustomPrompt(event.target.value)}
              placeholder="描述这批评论共同的语气、角度、长短、是否允许玩梗、需要避免的内容等。"
              value={customPrompt}
            />
          </label>

          <button
            className="mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-slate-300 px-4 text-sm font-semibold text-white"
            disabled
            title="批量评论生成接口将在阶段 2 接入"
            type="button"
          >
            <PlayIcon />
            批量生成预热评论
          </button>
        </section>
      </div>

      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 3</div>
            <h2 className="mt-1 text-base font-semibold text-slate-950">按帖子查看评论结果</h2>
            <p className="mt-1 text-sm text-slate-500">结果将按帖子分组，每组评论可以分别编辑、复制、删除和导出。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-600">
              帖子 0 / {urlPreview.valid.length}
            </span>
            <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-600">
              评论 0 / {plannedCommentCount}
            </span>
          </div>
        </div>

        <div className="mt-4 rounded-md border border-dashed border-slate-300 px-5 py-12 text-center text-sm font-medium text-slate-500">
          完成批量帖子感知并提交统一生成要求后，每个帖子的评论组将显示在这里。
        </div>
      </section>
    </div>
  )
}

type WarmupUrlPreview = {
  rawCount: number
  valid: string[]
  invalid: string[]
  duplicateCount: number
}

function parseWarmupPostUrls(rawText: string): WarmupUrlPreview {
  const lines = rawText
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean)
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

  return {
    rawCount: lines.length,
    valid,
    invalid,
    duplicateCount,
  }
}

function isValidRedditPostUrl(value: string): boolean {
  if (!isValidRedditUrl(value)) {
    return false
  }
  try {
    const parsed = new URL(value)
    const host = parsed.hostname.toLowerCase()
    if (host === 'redd.it' || host.endsWith('.redd.it')) {
      return Boolean(parsed.pathname.replaceAll('/', ''))
    }
    return parsed.pathname.toLowerCase().includes('/comments/')
  } catch {
    return false
  }
}

function clampCount(value: string): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    return 20
  }
  return Math.min(MAX_WARMUP_COMMENT_COUNT, Math.max(1, Math.trunc(parsed)))
}

function PreviewMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-left">
      <div className="text-[11px] font-semibold text-slate-500">{label}</div>
      <div className="mt-1 text-base font-semibold text-slate-900">{value}</div>
    </div>
  )
}

const inputClassName =
  'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-950 outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-100'
