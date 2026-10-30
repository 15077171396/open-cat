import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  return { apiGet: get }
})

import { apiGet } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import Workplace from './index'

const mockedGet = vi.mocked(apiGet)

const TODOS = {
  total: 3,
  groups: [
    {
      key: 'feedback',
      label: '用户反馈待处理',
      count: 3,
      path: '/feedback',
      severity: 'warning',
      detail: '',
    },
  ],
}

const DASHBOARD = { today_registrations: 2, total_users: 6, today_downloads: 0 }

const AUDIT = {
  items: [
    {
      id: 1,
      admin_account: 'admin@example.com',
      action_label: '更新系统设置',
      target_type_label: '系统设置',
      summary: '更新系统设置',
      risk_level: 'normal',
      created_at: '2026-10-31 00:00:00',
    },
  ],
}

const TUTORIALS = {
  items: [
    {
      id: 9,
      title: 'ComfyUI 零基础入门',
      status: 'published',
      category: 'ComfyUI',
      updated_at: '2026-10-30 10:00:00',
    },
  ],
  total: 1,
}

function renderPage() {
  return render(
    <MemoryRouter>
      <Workplace />
    </MemoryRouter>,
  )
}

describe('Workplace', () => {
  beforeEach(() => {
    useAuthStore.setState({
      isSuper: true,
      permissions: [],
      user: { id: 1, account: 'admin@example.com', role: 'admin' } as never,
    })
    mockedGet.mockReset()
    mockedGet.mockImplementation((url: string) => {
      if (url === '/admin/todos') return Promise.resolve(TODOS)
      if (url === '/admin/dashboard') return Promise.resolve(DASHBOARD)
      if (url === '/admin/audit-logs') return Promise.resolve(AUDIT)
      if (url === '/admin/tutorials') return Promise.resolve(TUTORIALS)
      return Promise.resolve({})
    })
  })

  afterEach(() => {
    useAuthStore.setState({ isSuper: false, permissions: null, user: null })
  })

  it('展示待办、快捷入口、最近动态与教程', async () => {
    renderPage()
    expect(await screen.findByText('用户反馈待处理')).toBeInTheDocument()
    expect(screen.getByText('新建教程')).toBeInTheDocument()
    expect(await screen.findByText('更新系统设置')).toBeInTheDocument()
    expect(await screen.findByText('ComfyUI 零基础入门')).toBeInTheDocument()
    expect(screen.getAllByText('待我处理').length).toBeGreaterThan(0)
  })

  it('没有审计权限时不请求审计接口并显示提示', async () => {
    useAuthStore.setState({
      isSuper: false,
      permissions: ['dashboard:view', 'tutorial:view'],
      user: { id: 2, account: 'operator@example.com', role: 'admin' } as never,
    })
    renderPage()
    expect(await screen.findByText('需要「操作审计」查看权限')).toBeInTheDocument()
    expect(mockedGet).not.toHaveBeenCalledWith('/admin/audit-logs', expect.anything())
  })
})
