import type { PostInput } from '../types'
import { createPostInput, dedupePosts, removeInvalidPosts, validatePosts } from '../lib/validation'
import { PlusIcon, TrashIcon } from './icons'

type ManualPostEditorProps = {
  posts: PostInput[]
  onChange: (posts: PostInput[]) => void
}

export function ManualPostEditor({ posts, onChange }: ManualPostEditorProps) {
  const updatePost = (id: string, patch: Partial<PostInput>) => {
    onChange(validatePosts(posts.map((post) => (post.id === id ? { ...post, ...patch, source: 'manual' } : post))))
  }

  const removePost = (id: string) => {
    const nextPosts = posts.filter((post) => post.id !== id)
    onChange(validatePosts(nextPosts.length ? nextPosts : [createPostInput('manual')]))
  }

  return (
    <section className="space-y-2">
      <div className="max-h-[70vh] min-h-[500px] space-y-1.5 overflow-y-auto pr-1">
        {posts.map((post, index) => (
          <div className="rounded-md border border-slate-200 bg-white p-1.5" key={post.id}>
            <div className="mb-1 flex items-center justify-between gap-3">
              <span className="text-[11px] font-semibold text-slate-500">#{index + 1}</span>
              <button
                aria-label="删除这一行"
                className="inline-flex h-5 w-5 items-center justify-center rounded-md text-slate-500 transition hover:bg-rose-50 hover:text-rose-600"
                onClick={() => removePost(post.id)}
                title="删除这一行"
                type="button"
              >
                <TrashIcon />
              </button>
            </div>

            <div className="space-y-2">
              <input
                className="h-7 w-full rounded-md border border-slate-300 px-2 text-[11px] text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-100"
                onChange={(event) => updatePost(post.id, { url: event.target.value })}
                placeholder="帖子链接"
                value={post.url}
              />
              {post.validationStatus !== 'valid' && (
                <p className="text-[11px] font-medium text-amber-700">{post.validationMessage}</p>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-1.5">
        <button
          className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700"
          onClick={() => onChange(validatePosts([...posts, createPostInput('manual')]))}
          type="button"
        >
          <PlusIcon />
          添加一行
        </button>
        <button
          className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700"
          onClick={() => onChange(removeInvalidPosts(posts))}
          type="button"
        >
          <TrashIcon />
          清空无效项
        </button>
        <button
          className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700"
          onClick={() => onChange(dedupePosts(posts))}
          type="button"
        >
          <TrashIcon />
          去重
        </button>
      </div>
    </section>
  )
}
