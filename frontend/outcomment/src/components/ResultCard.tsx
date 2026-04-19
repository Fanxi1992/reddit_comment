import ReactMarkdown from 'react-markdown'

import type { ResultItem } from '../types'
import { AlertIcon, CheckIcon, ClockIcon } from './icons'

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

export function ResultCard({ item }: ResultCardProps) {
  const displayTitle = item.backendTitle || '无标题'
  const community = item.communityName

  return (
    <article className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
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

          <h3 className="line-clamp-2 text-base font-semibold leading-6 text-slate-950">{displayTitle}</h3>
        </div>
      </div>

      <a
        className="block truncate text-sm font-medium text-teal-700 hover:text-teal-800"
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
        <div className="mt-4 rounded-md bg-slate-50 px-3 py-2 text-sm font-medium text-slate-700">{item.reason}</div>
      )}

      {item.textPreview && (
        <p className="mt-4 rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-600">{item.textPreview}</p>
      )}

      {item.analysis && (
        <div className="markdown-body mt-4 border-t border-slate-100 pt-4 text-sm leading-7 text-slate-700">
          <ReactMarkdown>{item.analysis}</ReactMarkdown>
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
