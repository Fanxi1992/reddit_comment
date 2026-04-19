import { useRef, useState } from 'react'

import { downloadExcelTemplate, parseExcelFile } from '../lib/excel'
import { validatePosts } from '../lib/validation'
import type { ImportReport, PostInput } from '../types'
import { DownloadIcon, UploadIcon } from './icons'

type ExcelImportPanelProps = {
  onImport: (posts: PostInput[]) => void
}

export function ExcelImportPanel({ onImport }: ExcelImportPanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [report, setReport] = useState<ImportReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleFileChange = async (file: File | undefined) => {
    if (!file) {
      return
    }

    setError(null)
    try {
      const parsed = await parseExcelFile(file)
      setReport(parsed.report)
      onImport(validatePosts(parsed.posts))
    } catch (exc) {
      setReport(null)
      setError(exc instanceof Error ? exc.message : 'Excel 解析失败')
    } finally {
      if (inputRef.current) {
        inputRef.current.value = ''
      }
    }
  }

  return (
    <section className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <button
          className="inline-flex h-11 items-center justify-center gap-2 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white transition hover:bg-slate-800"
          onClick={() => inputRef.current?.click()}
          type="button"
        >
          <UploadIcon />
          选择 XLSX
        </button>
        <button
          className="inline-flex h-11 items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700"
          onClick={downloadExcelTemplate}
          type="button"
        >
          <DownloadIcon />
          下载模板
        </button>
      </div>

      <input
        accept=".xlsx"
        className="hidden"
        onChange={(event) => void handleFileChange(event.target.files?.[0])}
        ref={inputRef}
        type="file"
      />

      {report && (
        <div className="grid grid-cols-4 gap-2 rounded-md border border-slate-200 bg-white p-3 text-center">
          <Metric label="总行数" value={report.totalRows} />
          <Metric label="有效" value={report.validRows} />
          <Metric label="重复" value={report.duplicateRows} />
          <Metric label="无效" value={report.invalidRows} />
        </div>
      )}

      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">
          {error}
        </div>
      )}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-lg font-semibold text-slate-950">{value}</div>
      <div className="text-xs font-medium text-slate-500">{label}</div>
    </div>
  )
}
