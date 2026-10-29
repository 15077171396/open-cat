import { describe, expect, it, vi } from 'vitest'
import { createEditorBridge } from './editorBridge'

function fakeQuill() {
  let selection: { index: number; length: number } | null = null
  const listeners: Array<(range: { index: number; length: number } | null) => void> = []
  return {
    getSelection: () => selection,
    setSelection: (index: number, length = 0) => { selection = { index, length } },
    getText: (index = 0, length?: number) => 'abcdef'.slice(index, length == null ? undefined : index + length),
    getSemanticHTML: () => '<p>abc</p>',
    deleteText: vi.fn(),
    insertEmbed: vi.fn(),
    formatText: vi.fn(),
    getModule: () => ({ dangerouslyPasteHTML: vi.fn() }),
    on: (_event: string, cb: (range: { index: number; length: number } | null) => void) => listeners.push(cb),
    off: vi.fn(),
    getBounds: () => ({ left: 0, top: 0, width: 100, height: 20 }),
    root: { querySelectorAll: () => [] },
    _emit: (range: { index: number; length: number } | null) => listeners.forEach((cb) => cb(range)),
  }
}

describe('createEditorBridge', () => {
  it('缓存最后一次非空选区', () => {
    const quill = fakeQuill()
    const bridge = createEditorBridge(quill as never)
    quill._emit({ index: 1, length: 2 })
    quill._emit(null)
    expect(bridge.getSelection()).toMatchObject({ index: 1, length: 2, text: 'bc' })
    bridge.dispose()
  })

  it('折叠选区也记录当前光标，失焦后保留落点（续写用）', () => {
    const quill = fakeQuill()
    const bridge = createEditorBridge(quill as never)
    quill._emit({ index: 1, length: 2 })
    expect(bridge.getCaret()).toBe(3)
    // 只是点了一下正文（无选中）也要更新落点
    quill._emit({ index: 4, length: 0 })
    expect(bridge.getCaret()).toBe(4)
    // 面板抢焦点后 Quill 发 null：落点与上次非空选区都不能丢
    quill._emit(null)
    expect(bridge.getCaret()).toBe(4)
    expect(bridge.getSelection()).toMatchObject({ index: 1, length: 2 })
    bridge.dispose()
  })

  it('替换前校验文本未漂移', () => {
    const quill = fakeQuill()
    const bridge = createEditorBridge(quill as never)
    quill._emit({ index: 1, length: 2 })
    expect(bridge.replaceRange(1, 2, '<p>X</p>')).toBe(true)
    expect(quill.deleteText).toHaveBeenCalledWith(1, 2, 'user')
    bridge.dispose()
  })

  it('文本漂移时拒绝替换', () => {
    const quill = fakeQuill()
    const bridge = createEditorBridge(quill as never)
    quill._emit({ index: 0, length: 2 })
    expect(bridge.replaceRange(0, 2, '<p>X</p>', 'ZZ')).toBe(false)
    expect(quill.deleteText).not.toHaveBeenCalled()
    bridge.dispose()
  })
})

describe('createEditorBridge 补充契约', () => {
  it('dispose 注销 selection-change 监听', () => {
    const quill = fakeQuill()
    const bridge = createEditorBridge(quill as never)
    bridge.dispose()
    expect(quill.off).toHaveBeenCalledWith('selection-change', expect.any(Function))
  })

  it('insertHtml 无选区时插入文末，且使用 user source', () => {
    const quill = fakeQuill()
    const paste = vi.fn()
    quill.getModule = () => ({ dangerouslyPasteHTML: paste })
    const bridge = createEditorBridge(quill as never)
    bridge.insertHtml('<p>X</p>')
    expect(paste).toHaveBeenCalledWith(5, '<p>X</p>', 'user')
    bridge.dispose()
  })

  it('insertImage 以 user source 插入并写入 alt', () => {
    const quill = fakeQuill()
    const bridge = createEditorBridge(quill as never)
    quill.setSelection(2, 0)
    bridge.insertImage('https://cdn.example.com/a.png', '示意图')
    expect(quill.insertEmbed).toHaveBeenCalledWith(2, 'image', 'https://cdn.example.com/a.png', 'user')
    expect(quill.formatText).toHaveBeenCalledWith(2, 1, { alt: '示意图' }, 'user')
    bridge.dispose()
  })
})
