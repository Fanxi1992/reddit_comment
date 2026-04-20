import { PlayIcon, StopIcon } from './icons'

type AnalysisFormProps = {
  prompt: string
  validCount: number
  maxBatchPosts: number
  isRunning: boolean
  onPromptChange: (prompt: string) => void
  onSubmit: () => void
  onCancel: () => void
}

const PROMPT_PLACEHOLDER =
  '这里请根据你实际产品和客户的需求，键入针对性的提示词。\n\nGemini 已经获得了当前帖子的全部内容，包括文本和图片（如有）。你需要编辑提示词，让 Gemini 生成符合要求的截流评论。\n\n常见需要提及的内容包括：任务背景、客户的特殊要求、评论长短控制、语气控制、移动端打字特点等。'

export function AnalysisForm({
  prompt,
  validCount,
  maxBatchPosts,
  isRunning,
  onPromptChange,
  onSubmit,
  onCancel,
}: AnalysisFormProps) {
  const isOverLimit = validCount > maxBatchPosts
  const canSubmit = validCount > 0 && prompt.trim().length > 0 && !isRunning && !isOverLimit

  return (
    <section className="rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-950">针对性评论生成提示词</h2>
          <p className="mt-1 text-sm text-slate-500">
            当前提示词会应用到下方所有有效帖子，单批最多 {maxBatchPosts} 条，重复链接不会提交。
          </p>
        </div>
        <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-500">{prompt.length} 字符</span>
      </div>

      {isOverLimit && (
        <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800">
          当前有 {validCount} 条有效链接，超过单批上限 {maxBatchPosts} 条，请减少或去重后再提交。
        </div>
      )}

      <div className="grid gap-3 xl:grid-cols-[1fr_180px]">
        <div>
          <textarea
            className="min-h-36 w-full resize-y rounded-md border border-slate-300 bg-white px-3 py-2.5 text-sm leading-6 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-100"
            onChange={(event) => onPromptChange(event.target.value)}
            placeholder={PROMPT_PLACEHOLDER}
            value={prompt}
          />
        </div>

        <div className="flex items-end">
          {isRunning ? (
            <button
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white transition hover:bg-slate-800"
              onClick={onCancel}
              type="button"
            >
              <StopIcon />
              停止
            </button>
          ) : (
            <button
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-teal-600 px-4 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              disabled={!canSubmit}
              onClick={onSubmit}
              type="button"
            >
              <PlayIcon />
              开始分析
            </button>
          )}
        </div>
      </div>
    </section>
  )
}
