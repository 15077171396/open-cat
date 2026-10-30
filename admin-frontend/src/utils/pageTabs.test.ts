import { describe, expect, it } from 'vitest'
import {
  HOME_TAB,
  MAX_TABS,
  addTab,
  closeAllTabs,
  closeOtherTabs,
  limitTabs,
  nextTabAfterClose,
  normalizeTabs,
  readTabs,
  removeTab,
  writeTabs,
} from './pageTabs'

function memoryStorage() {
  let value: string | null = null
  return {
    getItem: () => value,
    setItem: (_key: string, next: string) => {
      value = next
    },
  }
}

describe('pageTabs', () => {
  it('首页固定在最前且去重', () => {
    expect(normalizeTabs(['/users', '/users', '/roles'])).toEqual([HOME_TAB, '/users', '/roles'])
    expect(normalizeTabs([HOME_TAB, '/users'])).toEqual([HOME_TAB, '/users'])
  })

  it('addTab 不重复添加，超过上限淘汰最旧的非当前页', () => {
    expect(addTab([HOME_TAB, '/users'], '/users')).toEqual([HOME_TAB, '/users'])
    const many = [HOME_TAB, ...Array.from({ length: MAX_TABS }, (_, i) => `/p${i}`)]
    const next = addTab(many, '/new')
    expect(next).toContain('/new')
    expect(next).toContain(HOME_TAB)
    expect(next.length).toBe(MAX_TABS)
    expect(next).not.toContain('/p0')
  })

  it('removeTab 不允许移除首页', () => {
    expect(removeTab([HOME_TAB, '/users'], HOME_TAB)).toEqual([HOME_TAB, '/users'])
    expect(removeTab([HOME_TAB, '/users'], '/users')).toEqual([HOME_TAB])
  })

  it('关闭激活标签回到左侧相邻标签，关闭非激活标签不跳转', () => {
    const tabs = [HOME_TAB, '/users', '/roles']
    expect(nextTabAfterClose(tabs, '/roles', '/roles')).toBe('/users')
    expect(nextTabAfterClose(tabs, '/users', '/users')).toBe(HOME_TAB)
    expect(nextTabAfterClose(tabs, '/roles', '/users')).toBeNull()
  })

  it('关闭其他 / 关闭全部都保留首页', () => {
    expect(closeOtherTabs([HOME_TAB, '/users', '/roles'], '/roles')).toEqual([HOME_TAB, '/roles'])
    expect(closeAllTabs()).toEqual([HOME_TAB])
  })

  it('limitTabs 保留当前页与首页', () => {
    const many = [HOME_TAB, ...Array.from({ length: MAX_TABS }, (_, i) => `/p${i}`)]
    const next = limitTabs(many, '/p0')
    expect(next).toContain('/p0')
    expect(next).toContain(HOME_TAB)
    expect(next.length).toBe(MAX_TABS)
  })

  it('读写 sessionStorage；损坏或版本不符时回到首页', () => {
    const storage = memoryStorage()
    writeTabs(storage, ['/users', '/roles'])
    expect(readTabs(storage)).toEqual([HOME_TAB, '/users', '/roles'])

    storage.setItem('x', '{bad json')
    expect(readTabs(storage)).toEqual([HOME_TAB])

    storage.setItem('x', JSON.stringify({ v: 0, tabs: ['/users'] }))
    expect(readTabs(storage)).toEqual([HOME_TAB])
  })
})
