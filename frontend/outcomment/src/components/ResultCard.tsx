import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'

import type { ResultItem } from '../types'
import { AlertIcon, CheckIcon, ChevronDownIcon, ClockIcon, CopyIcon } from './icons'

type ResultCardProps = {
  item: ResultItem
}

const STATUS_TEXT = {
  queued: '等待',
  processing: '处理中',
  success: '成功',
  skipped: '跳过',
  failed: '失败',
}

const STATUS_CLASS = {
  queued: 'bg-slate-100 text-slate-600',
  processing: 'bg-teal-50 text-teal-700',
  success: 'bg-emerald-50 text-emerald-700',
  skipped: 'bg-amber-50 text-amber-700',
  failed: 'bg-rose-50 text-rose-700',
}

const COLLAPSE_THRESHOLD = 900

export function ResultCard({ item }: ResultCardProps) {
  const displayTitle = item.backendTitle || '无标题'
  const community = item.communityName
  const analysis = item.analysis ?? ''
  const shouldCollapse = analysis.length > COLLAPSE_THRESHOLD
  const [isExpanded, setIsExpanded] = useState(!shouldCollapse)
  const [copyLabel, setCopyLabel] = useState('复制')

  const analysisClassName = useMemo(() => {
    const baseClass =
      'markdown-body min-w-0 max-w-full overflow-hidden border-t border-slate-100 pt-4 text-sm leading-7 text-slate-700'
    if (!shouldCollapse || isExpanded) {
      return `${baseClass} mt-4`
    }
    return `${baseClass} max-h-[180px]`
  }, [isExpanded, shouldCollapse])

  const copyAnalysis = async () => {
    if (!analysis) {
      return
    }

    try {
      await navigator.clipboard.writeText(analysis)
      setCopyLabel('已复制')
      window.setTimeout(() => setCopyLabel('复制'), 1400)
    } catch {
      setCopyLabel('复制失败')
      window.setTimeout(() => setCopyLabel('复制'), 1400)
    }
  }

  return (
    <article className="min-w-0 max-w-full overflow-hidden rounded-md border border-slate-200 bg-white p-3.5 shadow-sm">
      <div className="mb-3 flex min-w-0 flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${STATUS_CLASS[item.status]}`}>
              <StatusIcon status={item.status} />
              {STATUS_TEXT[item.status]}
            </span>
            {community && (
              <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-600">{community}</span>
            )}
            {typeof item.imageCount === 'number' && item.imageCount > 0 && (
              <span className="rounded-md bg-indigo-50 px-2 py-1 text-xs font-semibold text-indigo-700">
                图片 {item.imageCount}
              </span>
            )}
          </div>

          <h3 className="line-clamp-2 break-words text-base font-semibold leading-6 text-slate-950">{displayTitle}</h3>
        </div>
      </div>

      <a
        className="block max-w-full truncate text-sm font-medium text-teal-700 hover:text-teal-800"
        href={item.input.url}
        rel="noreferrer"
        target="_blank"
        title={item.input.url}
      >
        {item.input.url}
      </a>

      {item.status === 'processing' && (
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100">
          <div className="h-full w-1/2 animate-pulse rounded-full bg-teal-500" />
        </div>
      )}

      {item.reason && (
        <div className="mt-4 break-words rounded-md bg-slate-50 px-3 py-2 text-sm font-medium text-slate-700">{item.reason}</div>
      )}

      {item.textPreview && (
        <p className="mt-4 break-words rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-600">{item.textPreview}</p>
      )}

      {analysis && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-semibold text-slate-500">Gemini 分析</span>
            <div className="flex flex-wrap gap-2">
              <button
                className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-600 transition hover:border-teal-300 hover:text-teal-700"
                onClick={() => void copyAnalysis()}
                type="button"
              >
                <CopyIcon className="h-3.5 w-3.5" />
                {copyLabel}
              </button>
              {shouldCollapse && (
                <button
                  className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-600 transition hover:border-teal-300 hover:text-teal-700"
                  onClick={() => setIsExpanded((current) => !current)}
                  type="button"
                >
                  <ChevronDownIcon className={`h-3.5 w-3.5 transition ${isExpanded ? 'rotate-180' : ''}`} />
                  {isExpanded ? '收起' : '展开'}
                </button>
              )}
            </div>
          </div>

          <div className="relative">
            <div className={analysisClassName}>
              <ReactMarkdown>{analysis}</ReactMarkdown>
            </div>
            {shouldCollapse && !isExpanded && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-white to-transparent" />
            )}
          </div>
        </div>
      )}
    </article>
  )
}

function StatusIcon({ status }: { status: ResultItem['status'] }) {
  if (status === 'success') {
    return <CheckIcon className="h-3.5 w-3.5" />
  }

  if (status === 'failed') {
    return <AlertIcon className="h-3.5 w-3.5" />
  }

  return <ClockIcon className="h-3.5 w-3.5" />
}
