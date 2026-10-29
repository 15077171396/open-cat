/**
 * Markdown / HTML → 消毒后的 HTML（供第三方导入与各内容编辑页调用）。
 * 安全边界：这是后台编辑器的消毒口径（同一信任域）；前台展示走 RichText 的更严格白名单，勿复用。
 */

import DOMPurify from 'dompurify'
import { marked } from 'marked'

import { DOMPURIFY_CONFIG } from './quillUtils'

/** Markdown → 消毒后的 HTML；HTML 块原样透传（与 GitHub 渲染行为一致） */
export function markdownToHtml(content: string): string {
  if (!content) return ''
  return normalizeTableMarkup(
    DOMPurify.sanitize(marked.parse(content, { async: false }) as string, DOMPURIFY_CONFIG),
  )
}

/** Markdown / HTML → 消毒后的 HTML：以常见 HTML 标签开头则视为 HTML，否则先经 marked 转换 */
export function markdownToSafeHtml(content: string): string {
  if (!content) return ''
  if (/^\s*<(p|h[1-6]|ul|ol|div|blockquote|pre|table|img|figure|hr)\b/i.test(content)) {
    return normalizeTableMarkup(DOMPurify.sanitize(content, DOMPURIFY_CONFIG))
  }
  return markdownToHtml(content)
}

/** 表格块（非贪婪，逐块处理；嵌套表格极罕见，不为其增加复杂度） */
const TABLE_BLOCK_RE = /<table\b[^>]*>[\s\S]*?<\/table>/gi

/**
 * 表格结构标签与下一个标签之间的**纯空白文本节点**（含换行）。
 * 只匹配表格结构标签，不碰 td/th 内部的行内元素间距。
 */
const TABLE_STRUCT_GAP_RE =
  /(<\/?(?:table|thead|tbody|tfoot|tr|colgroup|col|td|th)\b[^>]*>)[ \t]*\r?\n[ \t]*(?=<)/gi

/**
 * 去掉表格结构标签之间的空白文本节点。
 *
 * 为什么必须做（2026-10-19 复现确认）：`marked` 输出的表格是
 * `<table><thead>…</thead>\n<tbody>…</tbody></table>`，`</thead>` 与 `<tbody>`
 * 之间那个换行在 HTML 里是**一个文本节点**。编辑器的 `quill-table-better`
 * 在把 HTML 转成 Delta 时，会把它当成表格内的一行「无属性内容」，于是
 * **一张表被劈成两张**：表头单独一张表 → 中间一个空段落（就是用户看到的
 * 「表头被隔开」）→ 表体又是一张表。去掉这段空白后仍是同一个 DOM 语义，
 * 但转换结果是一张完整的表（`thead` + `tbody`）。
 *
 * 只处理 `<table>` 块内部，且只在结构标签与 `<` 之间有空白时才动手，
 * 不会影响普通段落 / 行内元素的换行与间距。
 */
export function normalizeTableMarkup(html: string): string {
  if (!html || !html.includes('<table')) return html
  return html.replace(TABLE_BLOCK_RE, (block) => block.replace(TABLE_STRUCT_GAP_RE, '$1'))
}
