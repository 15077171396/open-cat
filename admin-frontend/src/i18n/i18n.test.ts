import { describe, expect, it } from 'vitest'
import { MESSAGES, hasMessage, isLocale, translate } from './messages'

/**
 * 词表守卫。
 *
 * 前端 i18n 最容易出的不是翻译质量，而是「加了 key 只补一种语言」——
 * 英文用户看到 key 原文，或中文用户被回退成英文。这里把两边的 key 集合钉死，
 * 并钉住插值与回退顺序（英文缺译必须回退中文，而不是渲染空白）。
 */
describe('i18n 词表', () => {
  it('中英词表 key 集合一致（新增文案必须两边都补）', () => {
    expect(Object.keys(MESSAGES['en-US']).sort()).toEqual(Object.keys(MESSAGES['zh-CN']).sort())
  })

  it('按语言取文案', () => {
    expect(translate('zh-CN', 'login.submit')).toBe('登录')
    expect(translate('en-US', 'login.submit')).toBe('Sign in')
  })

  it('插值替换占位符；缺变量时保留原占位符而不是输出 undefined', () => {
    expect(translate('zh-CN', 'common.total', { total: 12 })).toBe('共 12 条')
    expect(translate('en-US', 'common.total', { total: 3 })).toBe('Total 3')
    expect(translate('en-US', 'common.total', {})).toBe('Total {total}')
  })

  it('未知 key 原样返回（便于在界面上定位漏翻）', () => {
    expect(translate('zh-CN', 'no.such.key')).toBe('no.such.key')
    expect(translate('en-US', 'no.such.key')).toBe('no.such.key')
  })

  it('英文缺译时回退中文', () => {
    const key = 'common.save'
    const backup = MESSAGES['en-US'][key]
    delete MESSAGES['en-US'][key]
    try {
      expect(translate('en-US', key)).toBe('保存')
      expect(hasMessage('en-US', key)).toBe(false)
    } finally {
      MESSAGES['en-US'][key] = backup
    }
  })

  it('isLocale 只接受登记过的语言', () => {
    expect(isLocale('zh-CN')).toBe(true)
    expect(isLocale('en-US')).toBe(true)
    expect(isLocale('fr-FR')).toBe(false)
    expect(isLocale(undefined)).toBe(false)
  })
})
