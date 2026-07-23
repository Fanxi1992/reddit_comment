import { useState } from 'react'

import { PlayIcon, SparkIcon } from './icons'

export const MAX_WARMUP_COMMENT_COUNT = 40

export function WarmupCommentWorkspace() {
  const [postUrl, setPostUrl] = useState('')
  const [commentCount, setCommentCount] = useState('20')
  const [customPrompt, setCustomPrompt] = useState('')

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
              感知策划人员已经发布的 Reddit 帖子，根据自定义提示词一次生成多条相互独立的顶层评论。
            </p>
            <p className="mt-1 text-xs font-medium text-teal-700">
              当前为第一阶段页面骨架；帖子读取和评论生成将在下一阶段接入。
            </p>
          </div>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 1</div>
              <h2 className="mt-1 text-base font-semibold text-slate-950">读取 Reddit 帖子</h2>
              <p className="mt-1 text-sm text-slate-500">第一版每次处理一个策划人员自己发布的帖子。</p>
            </div>
            <span className="rounded-md bg-slate-100 px-2.5 py-1.5 text-xs font-semibold text-slate-500">单帖任务</span>
          </div>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">Reddit 帖子 URL</span>
            <div className="mt-1 flex flex-col gap-2 sm:flex-row">
              <input
                className={`${inputClassName} min-w-0 flex-1`}
                onChange={(event) => setPostUrl(event.target.value)}
                placeholder="https://www.reddit.com/r/.../comments/..."
                type="url"
                value={postUrl}
              />
              <button
                className="inline-flex h-10 shrink-0 items-center justify-center rounded-md bg-slate-300 px-4 text-sm font-semibold text-white"
                disabled
                title="帖子感知接口将在阶段 2 接入"
                type="button"
              >
                读取帖子
              </button>
            </div>
          </label>

          <div className="mt-4 rounded-md border border-dashed border-slate-300 bg-slate-50 px-5 py-10 text-center">
            <div className="text-sm font-semibold text-slate-700">等待读取帖子</div>
            <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500">
              读取后将在这里展示标题、正文摘要、帖子类型、图片数量以及已经感知到的评论数量。
            </p>
            <div className="mt-4 grid gap-2 sm:grid-cols-3">
              <PreviewMetric label="正文字符" value="-" />
              <PreviewMetric label="感知图片" value="-" />
              <PreviewMetric label="已加载评论" value="-" />
            </div>
          </div>
        </section>

        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 2</div>
            <h2 className="mt-1 text-base font-semibold text-slate-950">设置生成要求</h2>
            <p className="mt-1 text-sm text-slate-500">评论全部为互相独立的顶层评论，不生成评论树。</p>
          </div>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">评论数量</span>
            <input
              className={`${inputClassName} mt-1`}
              max={MAX_WARMUP_COMMENT_COUNT}
              min={1}
              onChange={(event) => setCommentCount(event.target.value)}
              type="number"
              value={commentCount}
            />
            <span className="mt-1 block text-xs text-slate-500">单次最多生成 {MAX_WARMUP_COMMENT_COUNT} 条。</span>
          </label>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-600">自定义提示词</span>
            <textarea
              className={`${inputClassName} mt-1 min-h-56 resize-y leading-6`}
              onChange={(event) => setCustomPrompt(event.target.value)}
              placeholder="描述评论的语气、角度、长短、是否允许玩梗、需要避免的内容等。"
              value={customPrompt}
            />
          </label>

          <button
            className="mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-slate-300 px-4 text-sm font-semibold text-white"
            disabled
            title="评论生成接口将在阶段 2 接入"
            type="button"
          >
            <PlayIcon />
            生成预热评论
          </button>
        </section>
      </div>

      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-teal-700">步骤 3</div>
            <h2 className="mt-1 text-base font-semibold text-slate-950">评论结果</h2>
            <p className="mt-1 text-sm text-slate-500">后续将在这里逐条编辑、复制、删除和导出生成结果。</p>
          </div>
          <span className="rounded-md bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-600">0 / {commentCount || 0}</span>
        </div>

        <div className="mt-4 rounded-md border border-dashed border-slate-300 px-5 py-12 text-center text-sm font-medium text-slate-500">
          完成帖子感知并提交生成要求后，评论将显示在这里。
        </div>
      </section>
    </div>
  )
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
