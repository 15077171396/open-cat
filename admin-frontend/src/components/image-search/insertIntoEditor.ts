/** 把转存后的图片与可选署名插入 Quill；所有变更使用 user source 以进入撤销栈。 */

import type { ImageInsertPayload } from './types'

type InsertSource = 'api' | 'silent' | 'user'

export interface QuillInsertLike {
  getLength: () => number
  insertEmbed: (index: number, format: string, value: unknown, source: InsertSource) => unknown
  formatText: (index: number, length: number, formats: Record<string, unknown>, source: InsertSource) => unknown
  insertText: (index: number, text: string, source: InsertSource) => unknown
  setSelection: (index: number, length: number, source: InsertSource) => unknown
}

export function insertImageIntoEditor(
  quill: QuillInsertLike,
  at: number,
  payload: ImageInsertPayload,
): number {
  const max = Math.max(0, quill.getLength() - 1)
  const index = Math.min(Math.max(0, at), max)
  quill.insertEmbed(index, 'image', payload.url, 'user')
  quill.formatText(index, 1, { alt: payload.alt }, 'user')

  let cursor = index + 1
  if (payload.credit?.text) {
    const creditText = `\n${payload.credit.text}`
    quill.insertText(cursor, creditText, 'user')
    const creditIndex = cursor + 1
    const formats: Record<string, unknown> = { italic: true }
    if (payload.credit.url) formats.link = payload.credit.url
    quill.formatText(creditIndex, payload.credit.text.length, formats, 'user')
    cursor += creditText.length
  }
  quill.setSelection(Math.min(cursor, Math.max(0, quill.getLength() - 1)), 0, 'silent')
  return index
}
