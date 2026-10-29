import type { WritingOperation } from './types'

/**
 * 后端 `context.article_text` 的上限是 50,000 字符。前端留出余量：模板还会拼标题、
 * 大纲等素材，顶到上限会被 Pydantic 直接 422，整次写作白跑。
 */
export const ARTICLE_TEXT_LIMIT = 45000

const TRUNCATION_MARKER = '\n\n……（全文过长，已截断）'

/** 这些操作的提示词消费 `{{article}}`，需要整篇正文而不是当前章节 */
const FULL_TEXT_OPERATIONS: ReadonlySet<WritingOperation> = new Set([
  'summarize', 'seo_description', 'titles', 'keywords',
])

/** 按总长度（含截断标记）截断，保证结果不超过 limit */
export function truncateArticleText(text: string, limit = ARTICLE_TEXT_LIMIT): string {
  if (text.length <= limit) return text
  const marker = TRUNCATION_MARKER.slice(0, Math.max(0, limit))
  return text.slice(0, Math.max(0, limit - marker.length)) + marker
}

/**
 * 教程页的正文上下文：
 * - 全文类操作（摘要 / SEO / 标题 / 关键词）发送整本教程的拼接正文，并在客户端截断；
 * - 选中段落 / 续写类操作只发送当前章节，避免把整本教程塞进一次改写请求。
 */
export function pickTutorialArticleText(
  operation: WritingOperation,
  fullText: string,
  chapterText: string,
): string {
  return truncateArticleText(FULL_TEXT_OPERATIONS.has(operation) ? fullText : chapterText)
}
