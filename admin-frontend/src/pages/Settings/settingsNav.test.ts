import { describe, expect, it } from 'vitest'
import {
  buildNav,
  categoryDirty,
  FALLBACK_CATEGORY_KEY,
  firstGroupKey,
  NAV_CATEGORIES,
} from './settingsNav'

const group = (key: string) => ({ key, label: `分组-${key}`, description: '', items: [] as unknown[] })

describe('buildNav', () => {
  it('把分组按分类归类，且顺序遵循 NAV_CATEGORIES 而不是后端顺序', () => {
    const nav = buildNav([group('seo'), group('basic'), group('storage')])
    expect(nav.map((c) => c.key)).toEqual(['basic', 'media', 'ops'])
    expect(nav[0].groups.map((g) => g.key)).toEqual(['basic'])
    expect(nav[1].groups.map((g) => g.key)).toEqual(['storage'])
  })

  it('每个后端分组只出现一次（不重复、不丢失）', () => {
    const keys = ['basic', 'business', 'invite', 'moderation', 'search', 'fetch', 'upload', 'storage']
    const nav = buildNav(keys.map(group))
    const flat = nav.flatMap((c) => c.groups.map((g) => g.key))
    expect(flat.sort()).toEqual([...keys].sort())
  })

  it('未归类的分组落到「其它」，而不是被静默丢弃', () => {
    const nav = buildNav([group('basic'), group('brand_new_group')])
    const other = nav.find((c) => c.key === FALLBACK_CATEGORY_KEY)
    expect(other?.groups.map((g) => g.key)).toEqual(['brand_new_group'])
  })

  it('后端已不存在的分组不会产生空分类', () => {
    const nav = buildNav([group('basic')])
    expect(nav.map((c) => c.key)).toEqual(['basic'])
    expect(nav.every((c) => c.groups.length > 0)).toBe(true)
  })

  it('声明里的 key 没有重复（重复会让同一分组渲染两次）', () => {
    const flat = NAV_CATEGORIES.flatMap((c) => c.groups)
    expect(flat.length).toBe(new Set(flat).size)
  })

  it('保留分组对象本身（页面需要 items 才能渲染表单）', () => {
    const nav = buildNav([{ ...group('basic'), items: [{ key: 'site_name' }] }])
    expect(nav[0].groups[0].items).toHaveLength(1)
  })

  it('AI 写作分组归入「AI」分类，且排在其它分类之后', () => {
    const nav = buildNav([group('ai_writing'), group('seo')])
    expect(nav.map((c) => c.key)).toEqual(['ops', 'ai'])
    expect(nav[1].label).toBe('AI')
    expect(nav[1].groups.map((g) => g.key)).toEqual(['ai_writing'])
  })
})

describe('firstGroupKey / categoryDirty', () => {
  it('默认选中第一个分类的第一项', () => {
    const nav = buildNav([group('seo'), group('basic')])
    expect(firstGroupKey(nav)).toBe('basic')
  })

  it('没有分组时返回空串（页面显示空态而不是崩溃）', () => {
    expect(firstGroupKey([])).toBe('')
  })

  it('分类内任一分组脏则整栏标脏', () => {
    const nav = buildNav([group('basic'), group('business')])
    expect(categoryDirty(nav[0], new Set())).toBe(false)
    expect(categoryDirty(nav[0], new Set(['business']))).toBe(true)
    expect(categoryDirty(nav[0], new Set(['storage']))).toBe(false)
  })
})
