import type { StreamSummary, TaskStage } from '../types'
import { AlertIcon, CheckIcon, ClockIcon, StopIcon } from './icons'

type ProgressPanelProps = {
  stage: TaskStage
  message: string
  validCount: number
  completedCount: number
  summary: StreamSummary | null
  error: string | null
}

const STAGE_LABELS: Record<TaskStage, string> = {
  idle: '等待提交',
  crawling: 'Apify 爬取中',
  analyzing: '逐帖分析中',
  completed: '完成',
  failed: '失败',
  cancelled: '已停止',
}

export function ProgressPanel({ stage, message, validCount, completedCount, summary, error }: ProgressPanelProps) {
  const denominator = summary?.total || validCount || 1
  const progress =
    stage === 'completed'
      ? 100
      : stage === 'crawling'
        ? 12
        : Math.min(96, Math.round((completedCount / denominator) * 100))

  return (
    <section className="rounded-md border border-slate-200 bg-white p-3.5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <StageIcon stage={stage} />
          <div>
            <h2 className="text-base font-semibold text-slate-950">{STAGE_LABELS[stage]}</h2>
            <p className="text-sm text-slate-500">{message || '准备就绪'}</p>
          </div>
        </div>
        <div className="text-sm font-semibold text-slate-600">
          {completedCount}/{denominator}
        </div>
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            stage === 'failed'
              ? 'bg-rose-500'
              : stage === 'completed'
                ? 'bg-emerald-500'
                : stage === 'cancelled'
                  ? 'bg-slate-400'
                  : 'bg-teal-500'
          }`}
          style={{ width: `${progress}%` }}
        />
      </div>

      {summary && (
        <div className="mt-3 grid grid-cols-4 gap-2 text-center">
          <Metric label="总数" value={summary.total} />
          <Metric label="成功" value={summary.processed} />
          <Metric label="跳过" value={summary.skipped} />
          <Metric label="失败" value={summary.failed} />
        </div>
      )}

      {error && <div className="mt-4 rounded-md bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">{error}</div>}
    </section>
  )
}

function StageIcon({ stage }: { stage: TaskStage }) {
  const className = 'h-5 w-5'
  const baseClass = 'inline-flex h-10 w-10 items-center justify-center rounded-md'

  if (stage === 'completed') {
    return (
      <span className={`${baseClass} bg-emerald-50 text-emerald-700`}>
        <CheckIcon className={className} />
      </span>
    )
  }

  if (stage === 'failed') {
    return (
      <span className={`${baseClass} bg-rose-50 text-rose-700`}>
        <AlertIcon className={className} />
      </span>
    )
  }

  if (stage === 'cancelled') {
    return (
      <span className={`${baseClass} bg-slate-100 text-slate-600`}>
        <StopIcon className={className} />
      </span>
    )
  }

  return (
    <span className={`${baseClass} bg-teal-50 text-teal-700`}>
      <ClockIcon className={className} />
    </span>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md bg-slate-50 px-2 py-2">
      <div className="text-base font-semibold text-slate-950">{value}</div>
      <div className="text-xs font-medium text-slate-500">{label}</div>
    </div>
  )
}
