import type { PostInput, PostPayload, PostSource } from '../types'

export function createPostInput(source: PostSource = 'manual', overrides: Partial<PostInput> = {}): PostInput {
  return {
    id: createId(),
    url: '',
    title: '',
    community: '',
    source,
    validationStatus: 'invalid',
    validationMessage: '请输入帖子链接',
    ...overrides,
  }
}

export function validatePosts(posts: PostInput[]): PostInput[] {
  const seen = new Map<string, string>()

  return posts.map((post) => {
    const url = post.url.trim()
    const title = post.title?.trim() ?? ''
    const community = post.community?.trim() ?? ''
    const normalizedUrl = normalizeUrl(url)

    if (!url) {
      return {
        ...post,
        url,
        title,
        community,
        validationStatus: 'invalid',
        validationMessage: '缺少帖子链接',
      }
    }

    if (!isValidRedditUrl(url)) {
      return {
        ...post,
        url,
        title,
        community,
        validationStatus: 'invalid',
        validationMessage: '链接格式无效',
      }
    }

    if (seen.has(normalizedUrl)) {
      return {
        ...post,
        url,
        title,
        community,
        validationStatus: 'duplicate',
        validationMessage: '重复链接',
      }
    }

    seen.set(normalizedUrl, post.id)

    return {
      ...post,
      url,
      title,
      community: community || deriveCommunityFromUrl(url),
      validationStatus: 'valid',
      validationMessage: undefined,
    }
  })
}

export function getSubmittablePosts(posts: PostInput[]): PostInput[] {
  return validatePosts(posts).filter((post) => post.validationStatus === 'valid')
}

export function toPostPayload(posts: PostInput[]): PostPayload[] {
  return getSubmittablePosts(posts).map((post) => ({
    url: post.url,
    title: post.title || undefined,
    community: post.community || undefined,
  }))
}

export function removeInvalidPosts(posts: PostInput[]): PostInput[] {
  return validatePosts(posts).filter((post) => post.validationStatus === 'valid')
}

export function dedupePosts(posts: PostInput[]): PostInput[] {
  const seen = new Set<string>()
  const deduped: PostInput[] = []

  for (const post of validatePosts(posts)) {
    const normalizedUrl = normalizeUrl(post.url)
    if (post.validationStatus === 'duplicate' || seen.has(normalizedUrl)) {
      continue
    }
    if (post.url.trim()) {
      seen.add(normalizedUrl)
    }
    deduped.push(post)
  }

  return validatePosts(deduped)
}

export function normalizeUrl(url: string): string {
  const trimmed = url.trim()

  try {
    const parsed = new URL(trimmed)
    parsed.hash = ''
    if (parsed.pathname.endsWith('/')) {
      parsed.pathname = parsed.pathname.slice(0, -1)
    }
    return parsed.toString().toLowerCase()
  } catch {
    return trimmed.toLowerCase()
  }
}

export function deriveCommunityFromUrl(url: string): string {
  try {
    const parsed = new URL(url)
    const match = parsed.pathname.match(/\/r\/([^/]+)/i)
    return match ? `r/${match[1]}` : ''
  } catch {
    return ''
  }
}

export function isValidRedditUrl(url: string): boolean {
  try {
    const parsed = new URL(url.trim())
    return (
      ['http:', 'https:'].includes(parsed.protocol) &&
      (parsed.hostname === 'reddit.com' ||
        parsed.hostname.endsWith('.reddit.com') ||
        parsed.hostname === 'redd.it' ||
        parsed.hostname.endsWith('.redd.it'))
    )
  } catch {
    return false
  }
}

function createId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}
