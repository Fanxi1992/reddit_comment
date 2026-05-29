import * as XLSX from 'xlsx'

import type { ImportReport, PostInput, ProductContext, RedditSearchResultItem, ResultItem } from '../types'
import { createPostInput, validatePosts } from './validation'

const SHEET_NAME = 'Reddit帖子导入模板'
const URL_HEADER = '帖子链接'
const RESULT_STATUS_LABELS = {
  queued: '等待',
  processing: '处理中',
  success: '成功',
  skipped: '跳过',
  failed: '失败',
}

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

export function downloadAnalysisResults(results: ResultItem[]): void {
  const rows = results.map((result) => ({
    处理状态: RESULT_STATUS_LABELS[result.status],
    帖子标题: result.backendTitle || '无标题',
    帖子链接: result.input.url,
    帖子社区: result.communityName || '',
    大模型回复: result.analysis || result.reason || '',
  }))

  const worksheet = XLSX.utils.json_to_sheet(rows, {
    header: ['处理状态', '帖子标题', '帖子链接', '帖子社区', '大模型回复'],
  })
  worksheet['!cols'] = [{ wch: 12 }, { wch: 48 }, { wch: 72 }, { wch: 20 }, { wch: 96 }]

  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, '分析结果')
  XLSX.writeFile(workbook, `reddit-analysis-results-${formatDateForFileName(new Date())}.xlsx`)
}

export function downloadRedditSearchResultsXlsx(
  productContext: ProductContext,
  results: RedditSearchResultItem[],
): void {
  const rows = buildSearchResultRows(productContext, results)
  const worksheet = XLSX.utils.json_to_sheet(rows)
  worksheet['!cols'] = [
    { wch: 28 },
    { wch: 72 },
    { wch: 18 },
    { wch: 12 },
    { wch: 12 },
    { wch: 12 },
    { wch: 18 },
    { wch: 12 },
    { wch: 12 },
    { wch: 72 },
    { wch: 60 },
    { wch: 24 },
  ]

  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, 'Reddit URL 汇总')
  XLSX.writeFile(workbook, `reddit-search-urls-${formatDateForFileName(new Date())}.xlsx`)
}

export function downloadRedditSearchResultsCsv(
  productContext: ProductContext,
  results: RedditSearchResultItem[],
): void {
  const worksheet = XLSX.utils.json_to_sheet(buildSearchResultRows(productContext, results))
  const csv = XLSX.utils.sheet_to_csv(worksheet)
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `reddit-search-urls-${formatDateForFileName(new Date())}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function buildSearchResultRows(productContext: ProductContext, results: RedditSearchResultItem[]) {
  return results.map((result) => ({
    产品名称: productContext.productName,
    产品情况: productContext.productDescription,
    来源Query: result.query,
    匹配Queries: result.matchedQueries.join(' | '),
    意图: result.queryIntent,
    优先级: `P${result.priority}`,
    时间范围: result.timeRange,
    结果序号: result.resultIndex,
    Subreddit: result.subreddit,
    帖子标题: result.title,
    帖子链接: result.postUrl,
    PostID: result.postId,
    发布时间: result.ageText,
    Votes: result.votes ?? '',
    Comments: result.comments ?? '',
  }))
}

function formatDateForFileName(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day}-${hour}${minute}`
}
