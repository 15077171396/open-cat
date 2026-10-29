import type { Quill } from 'react-quill-new'

export interface EditorSelectionSnapshot {
  index: number
  length: number
  text: string
}

export interface EditorBridgeApi {
  getSelection: () => EditorSelectionSnapshot | null
  /** 当前光标位置（选区终点 / 折叠光标）；编辑器失焦后保留最后一次位置 */
  getCaret: () => number | null
  getFullText: () => string
  getSemanticHtml: () => string
  getOutline: () => string[]
  replaceRange: (index: number, length: number, html: string, expectedText?: string) => boolean
  insertHtml: (html: string) => void
  insertImage: (url: string, alt: string) => void
  dispose: () => void
}

type QuillLike = Pick<Quill, 'getSelection' | 'getText' | 'getSemanticHTML' | 'deleteText' |
  'insertEmbed' | 'formatText' | 'getModule' | 'on' | 'off' | 'getBounds' | 'root'>

export function createEditorBridge(quill: QuillLike): EditorBridgeApi {
  // 面板/气泡抢走焦点后 Quill 会发 null 选区（range 为 null），
  // 必须保留最后一次非空选区，否则点开 AI 面板就丢掉了用户的选中文本。
  let last: EditorSelectionSnapshot | null = null
  // 光标位置单独记录：折叠选区（只是点了一下正文）也要能定位续写位置；
  // 失焦后的 null 事件同样不清空，否则「点正文 → 打开面板 → 续写」会丢掉落点。
  let caret: number | null = null

  const onSelection = (range: { index: number; length: number } | null) => {
    if (!range) return
    caret = range.index + range.length
    if (range.length <= 0) return
    last = { index: range.index, length: range.length, text: quill.getText(range.index, range.length) }
  }
  quill.on('selection-change', onSelection)

  const pasteAt = (index: number, html: string) => {
    // 走 clipboard 模块而非直接插 HTML 字符串：表格、自定义 blot 由 Quill 的 Delta 解析处理；
    // 'user' source 保证触发 text-change → onChange，撤销栈与未保存守卫都能感知。
    const clipboard = quill.getModule('clipboard') as { dangerouslyPasteHTML?: (i: number, html: string, source?: string) => void }
    clipboard.dangerouslyPasteHTML?.(index, html, 'user')
  }

  return {
    getSelection: () => last,
    getCaret: () => caret,
    getFullText: () => quill.getText(),
    getSemanticHtml: () => quill.getSemanticHTML(),
    getOutline: () => {
      const headings = (quill.root as HTMLElement).querySelectorAll('h1,h2,h3,h4,h5,h6')
      return Array.from(headings).map((el) => el.textContent?.trim() || '').filter(Boolean)
    },
    replaceRange: (index, length, html, expectedText) => {
      // 生成期间作者可能继续编辑，索引会漂移；替换前用原文核对，对不上宁可让调用方提示。
      const current = quill.getText(index, length)
      if (expectedText != null && current !== expectedText) return false
      quill.deleteText(index, length, 'user')
      pasteAt(index, html)
      return true
    },
    insertHtml: (html) => {
      const range = quill.getSelection(true) || { index: quill.getText().length - 1, length: 0 }
      pasteAt(range.index, html)
    },
    insertImage: (url, alt) => {
      const range = quill.getSelection(true) || { index: quill.getText().length - 1, length: 0 }
      quill.insertEmbed(range.index, 'image', url, 'user')
      if (alt) quill.formatText(range.index, 1, { alt }, 'user')
    },
    dispose: () => quill.off('selection-change', onSelection),
  }
}
