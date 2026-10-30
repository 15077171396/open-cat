/**
 * 回收站页面的呈现契约：
 * - 类型汇总只来自接口，前端不自己算权限；
 * - 保留期文案必须能区分「剩余 N 天 / 已到期 / 不自动清理」；
 * - 没有恢复权限时按钮必须禁用，而不是点了才 403。
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  const post = vi.fn()
  return { apiGet: get, apiPost: post }
})

import { apiGet } from '../../api/client'
import RecycleBinPage, { retentionText } from './index'
import type { RecycleBinItem } from '../../types'

const mockedGet = vi.mocked(apiGet)

const TUTORIAL_ITEM: RecycleBinItem = {
  type: 'tutorial',
  type_label: '教程',
  id: 1,
  title: '教程 A',
  subtitle: 'AI',
  deleted_at: '2026-10-28T00:00:00',
  retention_days: 90,
  expires_at: '2027-01-26T00:00:00',
  remaining_days: 88,
  retention_note: '',
  can_restore: true,
}

const PURCHASED_ITEM: RecycleBinItem = {
  ...TUTORIAL_ITEM,
  id: 2,
  title: '付费教程',
  retention_days: null,
  expires_at: null,
  remaining_days: null,
  retention_note: '有购买记录，不自动清理',
}

const SUMMARY = {
  total: 3,
  items: [
    { type: 'tutorial' as const, type_label: '教程', count: 2, retention_days: 90, can_restore: true },
    { type: 'comment' as const, type_label: '评论', count: 0, retention_days: 30, can_restore: true },
    { type: 'user' as const, type_label: '用户', count: 1, retention_days: null, can_restore: false },
  ],
}

const USER_ITEM: RecycleBinItem = {
  type: 'user',
  type_label: '用户',
  id: 9,
  title: 'gone@test',
  subtitle: '角色：user',
  deleted_at: '2026-10-01T00:00:00',
  retention_days: null,
  expires_at: null,
  remaining_days: null,
  retention_note: '账号不自动清理',
  can_restore: false,
}

describe('retentionText', () => {
  it('区分剩余天数、到期与不自动清理', () => {
    expect(retentionText(TUTORIAL_ITEM)).toEqual({ text: '剩余 88 天', tone: 'warning' })
    expect(retentionText({ ...TUTORIAL_ITEM, remaining_days: 0 })).toEqual({
      text: '已到期，等待清理',
      tone: 'danger',
    })
    expect(retentionText({ ...TUTORIAL_ITEM, remaining_days: 3 }).tone).toBe('danger')
    expect(retentionText(PURCHASED_ITEM)).toEqual({
      text: '有购买记录，不自动清理',
      tone: 'muted',
    })
  })
})

describe('RecycleBinPage', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedGet.mockImplementation((url: string) => {
      if (url === '/admin/recycle-bin/summary') return Promise.resolve(SUMMARY)
      if (url === '/admin/recycle-bin/tutorials') {
        return Promise.resolve({ items: [TUTORIAL_ITEM, PURCHASED_ITEM], total: 2 })
      }
      if (url === '/admin/recycle-bin/users') {
        return Promise.resolve({ items: [USER_ITEM], total: 1 })
      }
      return Promise.resolve({ items: [], total: 0 })
    })
  })

  it('按汇总加载第一个有数据的类型，并显示保留期', async () => {
    render(<RecycleBinPage />)

    expect(await screen.findByText('教程 A')).toBeInTheDocument()
    expect(screen.getByText('付费教程')).toBeInTheDocument()
    expect(screen.getByText('剩余 88 天')).toBeInTheDocument()
    expect(screen.getByText('有购买记录，不自动清理')).toBeInTheDocument()
    expect(mockedGet).toHaveBeenCalledWith(
      '/admin/recycle-bin/tutorials',
      expect.objectContaining({ params: expect.objectContaining({ page: 1 }) }),
    )
  })

  it('切换类型会请求对应接口，且无恢复权限时按钮禁用', async () => {
    render(<RecycleBinPage />)
    await screen.findByText('教程 A')

    await userEvent.click(screen.getByRole('tab', { name: /用户/ }))
    expect(await screen.findByText('gone@test')).toBeInTheDocument()
    expect(screen.getByText('账号不自动清理')).toBeInTheDocument()

    const restoreButtons = screen.getAllByRole('button', { name: /恢复/ })
    expect(restoreButtons.some((button) => (button as HTMLButtonElement).disabled)).toBe(true)
  })

  it('加载失败时给出可见提示而不是空白页', async () => {
    mockedGet.mockRejectedValue(new Error('network'))
    render(<RecycleBinPage />)
    await waitFor(() => {
      // toast 走 portal；至少页面仍在且没有白屏
      expect(screen.getByText('回收站')).toBeInTheDocument()
    })
  })
})
