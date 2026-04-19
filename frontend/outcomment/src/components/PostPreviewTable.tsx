import type { PostInput } from '../types'

type PostPreviewTableProps = {
  posts: PostInput[]
}

const STATUS_LABELS = {
  valid: '有效',
  invalid: '无效',
  duplicate: '重复',
}

const STATUS_STYLES = {
  valid: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  invalid: 'bg-rose-50 text-rose-700 ring-rose-200',
  duplicate: 'bg-amber-50 text-amber-700 ring-amber-200',
}

export function PostPreviewTable({ posts }: PostPreviewTableProps) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-950">帖子预览</h2>
        <span className="text-xs font-semibold text-slate-500">{posts.length} 条</span>
      </div>

      <div className="max-h-80 overflow-auto rounded-md border border-slate-200 bg-white">
        <table className="min-w-[760px] w-full table-fixed text-left text-sm">
          <thead className="sticky top-0 z-10 bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-14 px-3 py-3">#</th>
              <th className="w-[42%] px-3 py-3">帖子链接</th>
              <th className="w-[22%] px-3 py-3">帖子标题</th>
              <th className="w-[16%] px-3 py-3">帖子社区</th>
              <th className="w-24 px-3 py-3">状态</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {posts.length === 0 ? (
              <tr>
                <td className="px-3 py-6 text-center text-slate-500" colSpan={5}>
                  暂无帖子
                </td>
              </tr>
            ) : (
              posts.map((post, index) => (
                <tr className="align-top" key={post.id}>
                  <td className="px-3 py-3 text-slate-500">{index + 1}</td>
                  <td className="px-3 py-3">
                    <div className="truncate font-medium text-slate-900" title={post.url}>
                      {post.url || '-'}
                    </div>
                    {post.validationMessage && (
                      <div className="mt-1 text-xs font-medium text-slate-500">{post.validationMessage}</div>
                    )}
                  </td>
                  <td className="px-3 py-3 text-slate-600">
                    <div className="truncate" title={post.title}>
                      {post.title || '-'}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-slate-600">{post.community || '-'}</td>
                  <td className="px-3 py-3">
                    <span
                      className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ring-1 ${
                        STATUS_STYLES[post.validationStatus]
                      }`}
                    >
                      {STATUS_LABELS[post.validationStatus]}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
