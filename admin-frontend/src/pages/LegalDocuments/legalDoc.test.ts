import { describe, expect, it } from 'vitest'
import { docState, plainTextLength, type LegalDocMeta } from './legalDoc'

const doc: LegalDocMeta = {
  key: 'legal_privacy',
  type: 'privacy',
  title: '隐私政策',
  is_custom: false,
  length: 4,
  updated_at: null,
  public_hint: '前台页脚 → 隐私政策',
  public_api: '/api/v1/settings/legal/privacy',
}

describe('法律文档：字数与状态', () => {
  it('字数只算正文，不含标签与 HTML 实体', () => {
    expect(plainTextLength('<p>甲方<b>乙方</b></p>')).toBe(4)
    expect(plainTextLength('<p>a&nbsp;b</p>')).toBe(2)
    expect(plainTextLength('')).toBe(0)
    expect(plainTextLength(null)).toBe(0)
    // 排版用的换行/缩进不计入
    expect(plainTextLength('<p>\n  甲方\n</p>')).toBe(2)
  })

  it('草稿为空时按系统默认文案呈现（不是 0 字空白）', () => {
    const state = docState(doc, '', '<p>旧值</p>', '<p>默认文案四字</p>')
    expect(state.isCustom).toBe(false)
    expect(state.length).toBe(6)
  })

  it('草稿非空即为自定义，字数跟草稿走', () => {
    const state = docState(doc, '<p>新文本</p>', '', '<p>默认文案四字</p>')
    expect(state.isCustom).toBe(true)
    expect(state.length).toBe(3)
  })

  it('pending 只在真正改过时为真', () => {
    expect(docState(doc, '<p>a</p>', '<p>a</p>', '').pending).toBe(false)
    expect(docState(doc, '<p>b</p>', '<p>a</p>', '').pending).toBe(true)
    expect(docState(doc, '', '<p>a</p>', '').pending).toBe(true) // 清空 = 恢复默认，也是改动
  })
})
