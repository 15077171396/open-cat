import { describe, expect, it, vi } from 'vitest'
import { insertImageIntoEditor } from './insertIntoEditor'

function fakeQuill(length = 20) {
  return {
    getLength: vi.fn(() => length),
    insertEmbed: vi.fn(),
    formatText: vi.fn(),
    insertText: vi.fn(),
    setSelection: vi.fn(),
  }
}

describe('insertImageIntoEditor', () => {
  it('inserts image, alt, credit and places the cursor after it', () => {
    const quill = fakeQuill(100)
    const creditText = '图片：Alice / Pexels'
    insertImageIntoEditor(quill, 5, {
      url: 'https://cos.example/a.webp',
      alt: '一只猫',
      credit: {
        text: creditText, url: 'https://www.pexels.com/photo/1/',
        license: 'Pexels License', license_url: '', requires_attribution: false,
      },
    })

    expect(quill.insertEmbed).toHaveBeenCalledWith(5, 'image', 'https://cos.example/a.webp', 'user')
    expect(quill.formatText).toHaveBeenNthCalledWith(1, 5, 1, { alt: '一只猫' }, 'user')
    expect(quill.insertText).toHaveBeenCalledWith(6, `\n${creditText}`, 'user')
    expect(quill.formatText).toHaveBeenNthCalledWith(2, 7, creditText.length, {
      italic: true, link: 'https://www.pexels.com/photo/1/',
    }, 'user')
    expect(quill.setSelection).toHaveBeenCalledWith(6 + 1 + creditText.length, 0, 'silent')
  })

  it('omits credit when attribution is off', () => {
    const quill = fakeQuill()
    insertImageIntoEditor(quill, 0, {
      url: 'https://cos.example/a.webp', alt: 'cat', credit: null,
    })
    expect(quill.insertText).not.toHaveBeenCalled()
  })

  it('clamps out-of-range insertion positions', () => {
    const quill = fakeQuill(5)
    insertImageIntoEditor(quill, 999, {
      url: 'https://cos.example/a.webp', alt: 'cat', credit: null,
    })
    expect(quill.insertEmbed).toHaveBeenCalledWith(4, 'image', 'https://cos.example/a.webp', 'user')
  })
})
