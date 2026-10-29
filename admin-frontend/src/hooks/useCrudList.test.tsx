/**
 * `useCrudList` 的行为契约测试。
 *
 * 重点是 `paramsKey`（= JSON.stringify(params)）那套依赖设计，它有两个必须被守住的后果：
 *
 * 1. **params 内容变化 → 重新请求**：key 变了，load 重建，effect 重新跑。
 * 2. **params 内容不变、引用变化 → 不重复请求**：调用方几乎都传内联字面量
 *    （`params: { status: 'open' }`），每次渲染都是新对象。如果有人把 `params` 直接放进
 *    依赖数组，这条会立刻挂红 —— 页面每次渲染都会打一遍接口。
 *
 * 这两条正是 `eslint-disable-next-line` 那条注释所保护的行为。注释只能解释，
 * 不能阻止别人改坏；测试可以。
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

type PagePayload = { items: { id: number }[]; total: number }

const apiGet = vi.fn<(url: string, config?: unknown) => Promise<{ items: { id: number }[]; total: number }>>(
  async () => ({ items: [{ id: 1 }], total: 1 }),
)
const apiDelete = vi.fn<(url: string) => Promise<unknown>>(async () => ({}))
const toastError = vi.fn()
const toastSuccess = vi.fn()

vi.mock('../api/client', () => ({
  default: {
    delete: (url: string) => apiDelete(url),
    put: vi.fn(async () => ({})),
    post: vi.fn(async () => ({})),
    get: vi.fn(async () => ({})),
  },
  apiGet: (url: string, config?: unknown) => apiGet(url, config),
}))

vi.mock('../utils/toast', () => ({
  toast: {
    error: (msg: string) => toastError(msg),
    success: (msg: string) => toastSuccess(msg),
  },
}))

// antd Modal.confirm 变成同步执行 onOk：删除流程才是我们要测的（弹窗本身不是）
vi.mock('antd', () => ({
  Modal: {
    confirm: ({ onOk }: { onOk?: () => void }) => {
      onOk?.()
    },
  },
}))

import { DEFAULT_PAGE_SIZE } from '../constants'
import { useCrudList } from './useCrudList'

const PAGE = { items: [{ id: 1 }], total: 30 }

describe('useCrudList · paramsKey 依赖语义', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiDelete.mockReset()
    toastError.mockReset()
    toastSuccess.mockReset()
    apiGet.mockResolvedValue(PAGE)
  })

  it('初始加载带上分页参数', async () => {
    const { result } = renderHook(() => useCrudList({ basePath: '/admin/tutorials' }))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(apiGet).toHaveBeenCalledTimes(1)
    expect(apiGet).toHaveBeenCalledWith('/admin/tutorials', {
      params: { page: 1, page_size: DEFAULT_PAGE_SIZE },
    })
  })

  it('params 内容变化 → 重新请求（key 变了）', async () => {
    const { result, rerender } = renderHook(
      ({ params }: { params: Record<string, string> }) =>
        useCrudList({ basePath: '/admin/tutorials', params }),
      { initialProps: { params: { status: 'open' } } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(apiGet).toHaveBeenCalledTimes(1)

    rerender({ params: { status: 'closed' } })
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2))
    // 新参数真的传下去了，而不是只触发了一次无效请求
    expect(apiGet).toHaveBeenLastCalledWith('/admin/tutorials', {
      params: { status: 'closed', page: 1, page_size: DEFAULT_PAGE_SIZE },
    })
  })

  it('params 内容不变、引用变化 → 不重复请求（内联字面量场景）', async () => {
    const { result, rerender } = renderHook(
      ({ params }: { params: Record<string, string> }) =>
        useCrudList({ basePath: '/admin/tutorials', params }),
      { initialProps: { params: { status: 'open' } } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(apiGet).toHaveBeenCalledTimes(1)

    // 每次 rerender 都是新对象，但内容一样 —— 如果依赖写成 params，这里会无限请求
    rerender({ params: { status: 'open' } })
    rerender({ params: { status: 'open' } })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(apiGet).toHaveBeenCalledTimes(1)
  })

  it('翻页时 params 保持生效，只换 page', async () => {
    const { result } = renderHook(() =>
      useCrudList({ basePath: '/admin/tutorials', params: { status: 'open' } }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.setPage(3))
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2))
    expect(apiGet).toHaveBeenLastCalledWith('/admin/tutorials', {
      params: { status: 'open', page: 3, page_size: DEFAULT_PAGE_SIZE },
    })
    expect(result.current.page).toBe(3)
  })

  it('paginated=false 时不带分页参数', async () => {
    const { result } = renderHook(() =>
      useCrudList({ basePath: '/admin/menus', paginated: false }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(apiGet).toHaveBeenCalledWith('/admin/menus', { params: {} })
    expect(apiGet).toHaveBeenCalledTimes(1)
  })

  it('快速翻页：旧响应被丢弃，只留最新一页', async () => {
    // 手动控制每个请求的 resolve 时机
    const pending: Array<(v: PagePayload | PromiseLike<PagePayload>) => void> = []
    apiGet.mockImplementation(
      () => new Promise<PagePayload>((resolve) => pending.push(resolve)),
    )

    const { result } = renderHook(() => useCrudList({ basePath: '/admin/tutorials' }))
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1))

    act(() => result.current.setPage(2))
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2))

    // 第 2 页先返回，第 1 页后返回（模拟网络乱序）
    act(() => pending[1]({ items: [{ id: 2 }], total: 30 }))
    await waitFor(() => expect(result.current.items).toEqual([{ id: 2 }]))

    act(() => pending[0]({ items: [{ id: 999 }], total: 30 }))
    // 过期响应不得覆盖新页
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.items).toEqual([{ id: 2 }])
    expect(result.current.page).toBe(2)
  })
})

describe('useCrudList · 删除后的页码回退', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiDelete.mockReset()
    apiGet.mockResolvedValue({ items: [{ id: 1 }], total: 21 })
  })

  it('删除当前页最后一条且不在第一页时回退一页（不重复请求旧页）', async () => {
    const { result } = renderHook(() => useCrudList({ basePath: '/admin/tutorials' }))
    await waitFor(() => expect(result.current.loading).toBe(false))

    // 先翻到第 2 页（此时 items 只有 1 条）
    act(() => result.current.setPage(2))
    await waitFor(() => expect(result.current.page).toBe(2))
    const callsBefore = apiGet.mock.calls.length

    await act(async () => {
      result.current.handleDelete(1)
    })
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/admin/tutorials/1'))
    // 回退到第 1 页，触发一次加载（而不是在空的第 2 页原地重刷）
    await waitFor(() => expect(result.current.page).toBe(1))
    expect(apiGet.mock.calls.length).toBeGreaterThan(callsBefore)
  })
})
