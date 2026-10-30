import { beforeEach, describe, expect, it } from 'vitest'
import { usePageTitleStore } from './pageTitleStore'

describe('pageTitleStore', () => {
  beforeEach(() => usePageTitleStore.getState().reset())

  it('按 pathname 写入标题', () => {
    usePageTitleStore.getState().setTitle('/users', '用户管理')
    expect(usePageTitleStore.getState().titles['/users']).toBe('用户管理')
  })

  it('改为相同标题时不产生新对象', () => {
    const store = usePageTitleStore.getState()
    store.setTitle('/users', '用户管理')
    const first = usePageTitleStore.getState().titles
    store.setTitle('/users', '用户管理')
    expect(usePageTitleStore.getState().titles).toBe(first)
  })

  it('clearTitle 只删指定路径，reset 清空', () => {
    const store = usePageTitleStore.getState()
    store.setTitle('/users', '用户管理')
    store.setTitle('/roles', '角色管理')
    store.clearTitle('/users')
    expect(usePageTitleStore.getState().titles).toEqual({ '/roles': '角色管理' })
    store.reset()
    expect(usePageTitleStore.getState().titles).toEqual({})
  })
})
