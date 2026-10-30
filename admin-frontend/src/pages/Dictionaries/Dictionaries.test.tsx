import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  const post = vi.fn()
  const put = vi.fn()
  const del = vi.fn()
  return { apiGet: get, apiPost: post, apiPut: put, apiDelete: del }
})

import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import Dictionaries from './index'

const mockedGet = vi.mocked(apiGet)
const mockedPost = vi.mocked(apiPost)
const mockedPut = vi.mocked(apiPut)
const mockedDelete = vi.mocked(apiDelete)

const TYPES = {
  items: [
    {
      id: 1,
      code: 'content_status',
      name: '内容状态',
      description: '教程发布状态',
      is_enabled: true,
      item_count: 2,
    },
    {
      id: 2,
      code: 'feedback_status',
      name: '反馈状态',
      description: null,
      is_enabled: true,
      item_count: 0,
    },
  ],
}

const ITEMS = [
  {
    id: 11,
    type_id: 1,
    label: '草稿',
    value: 'draft',
    sort_order: 0,
    is_enabled: true,
    is_default: false,
    remark: '仅后台可见',
  },
  {
    id: 12,
    type_id: 1,
    label: '已发布',
    value: 'published',
    sort_order: 1,
    is_enabled: true,
    is_default: true,
    remark: null,
  },
]

describe('Dictionaries', () => {
  beforeEach(() => {
    useAuthStore.setState({ isSuper: true, permissions: [] })
    mockedGet.mockReset()
    mockedPost.mockReset()
    mockedPut.mockReset()
    mockedDelete.mockReset()
    mockedGet.mockImplementation((url: string) => {
      if (url === '/admin/dicts') return Promise.resolve(TYPES)
      if (url === '/admin/dicts/1/items') return Promise.resolve(ITEMS)
      return Promise.resolve([])
    })
    mockedPost.mockResolvedValue({ id: 13 })
    mockedPut.mockResolvedValue({ ok: true })
    mockedDelete.mockResolvedValue({ ok: true })
  })

  afterEach(() => {
    useAuthStore.setState({ isSuper: false, permissions: null })
  })

  it('渲染字典类型与右侧字典项', async () => {
    render(<Dictionaries />)
    expect((await screen.findAllByText('内容状态')).length).toBeGreaterThan(0)
    expect(screen.getByText('反馈状态')).toBeInTheDocument()
    expect(await screen.findByText('草稿')).toBeInTheDocument()
    expect(screen.getByText('已发布')).toBeInTheDocument()
    expect(screen.getAllByText('content_status').length).toBeGreaterThan(0)
    expect(screen.getByText('published')).toBeInTheDocument()
  })

  it('切换字典项启用状态会调用接口', async () => {
    render(<Dictionaries />)
    await screen.findByText('草稿')
    await userEvent.click(screen.getByRole('switch', { name: '草稿 是否启用' }))
    await waitFor(() => {
      expect(mockedPut).toHaveBeenCalledWith('/admin/dicts/1/items/11', {
        is_enabled: false,
      })
    })
  })

  it('新建字典项会提交到当前类型并刷新', async () => {
    render(<Dictionaries />)
    await screen.findByText('草稿')
    await userEvent.click(screen.getByRole('button', { name: /新建字典项/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('标签'), '已下架')
    await userEvent.type(within(dialog).getByLabelText('值'), 'offline')
    await userEvent.click(within(dialog).getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/admin/dicts/1/items', {
        label: '已下架',
        value: 'offline',
        sort_order: 0,
        is_enabled: true,
        is_default: false,
        remark: null,
      })
    })
  })

  it('无权限时不显示新建与操作按钮（只读查看）', async () => {
    useAuthStore.setState({ isSuper: false, permissions: ['setting:view'] })
    render(<Dictionaries />)
    await screen.findByText('草稿')
    expect(screen.queryByRole('button', { name: /新建字典项/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /编辑/ })).not.toBeInTheDocument()
  })
})
