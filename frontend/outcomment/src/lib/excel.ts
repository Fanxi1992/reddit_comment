import * as XLSX from 'xlsx'

import type { ImportReport, PostInput } from '../types'
import { createPostInput, validatePosts } from './validation'

const SHEET_NAME = 'Reddit帖子导入模板'
const URL_HEADER = '帖子链接'

export async function parseExcelFile(file: File): Promise<{ posts: PostInput[]; report: ImportReport }> {
  const buffer = await file.arrayBuffer()
  const workbook = XLSX.read(buffer, { type: 'array' })
  const sheetName = workbook.SheetNames[0]

  if (!sheetName) {
    throw new Error('Excel 文件没有可读取的工作表')
  }

  const worksheet = workbook.Sheets[sheetName]
  const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(worksheet, {
    defval: '',
  })

  if (rows.length > 0 && !Object.prototype.hasOwnProperty.call(rows[0], URL_HEADER)) {
    throw new Error(`缺少必填列：${URL_HEADER}`)
  }

  const posts = rows
    .map((row) =>
      createPostInput('excel', {
        url: String(row[URL_HEADER] ?? '').trim(),
      }),
    )
    .filter((post) => post.url)

  const validatedPosts = validatePosts(posts)

  return {
    posts: validatedPosts,
    report: {
      totalRows: validatedPosts.length,
      validRows: validatedPosts.filter((post) => post.validationStatus === 'valid').length,
      duplicateRows: validatedPosts.filter((post) => post.validationStatus === 'duplicate').length,
      invalidRows: validatedPosts.filter((post) => post.validationStatus === 'invalid').length,
    },
  }
}

export function downloadExcelTemplate(): void {
  const worksheet = XLSX.utils.aoa_to_sheet([[URL_HEADER]])
  worksheet['!cols'] = [{ wch: 84 }]

  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, SHEET_NAME)
  XLSX.writeFile(workbook, 'reddit-post-import-template.xlsx')
}
