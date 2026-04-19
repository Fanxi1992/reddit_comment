import { PlayIcon, SparkIcon, StopIcon } from './icons'

type AnalysisFormProps = {
  prompt: string
  validCount: number
  isRunning: boolean
  onPromptChange: (prompt: string) => void
  onSubmit: () => void
  onCancel: () => void
}

const PROMPT_TEMPLATES = [
  {
    label: '用户痛点',
    value: '请分析这些 Reddit 帖子的用户痛点、抱怨、未被满足的需求，并整理成可用于产品和内容策略的洞察。',
  },
  {
    label: '营销角度',
    value: '请总结这些 Reddit 帖子的讨论焦点，提炼可用于营销文案、广告钩子和内容选题的切入点。',
  },
  {
    label: '竞品洞察',
    value: '请从这些 Reddit 帖子里识别竞品、替代方案、用户选择标准和潜在市场机会。',
  },
]

export function AnalysisForm({
  prompt,
  validCount,
  isRunning,
  onPromptChange,
  onSubmit,
  onCancel,
}: AnalysisFormProps) {
  const canSubmit = validCount > 0 && prompt.trim().length > 0 && !isRunning

  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-950">批次 Prompt</h2>
          <p className="mt-1 text-sm text-slate-500">当前 Prompt 会应用到下方所有有效帖子。</p>
        </div>
        <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-500">{prompt.length} 字符</span>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_220px]">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {PROMPT_TEMPLATES.map((template) => (
              <button
                className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-teal-300 hover:text-teal-700"
                key={template.label}
                onClick={() => onPromptChange(template.value)}
                type="button"
              >
                <SparkIcon />
                {template.label}
              </button>
            ))}
          </div>

          <textarea
            className="min-h-32 w-full resize-y rounded-md border border-slate-300 bg-white px-3 py-3 text-sm leading-6 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-100"
            onChange={(event) => onPromptChange(event.target.value)}
            placeholder="请分析这些 Reddit 帖子的用户痛点、讨论焦点、潜在营销切入点，并给出可执行的内容建议。"
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
