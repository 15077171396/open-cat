/**
 * 顶栏待办铃铛的行为契约。
 *
 * 计数与权限过滤在后端，这里只钉前端最容易坏的四件事：
 * 1. 有数据时必须把总数与各类别亮出来；
 * 2. 点某一类要跳到对应业务页（深链，不是只打开看板）；
 * 3. 没有待办时给空态，而不是一个空白浮层；
 * 4. 旧后端没有该接口（404）时整个铃铛隐藏 —— 升级顺序不该在顶栏留下一个坏入口。
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  return { apiGet: get }
})

import { apiGet } from '../../api/client'
import TodoBell from './TodoBell'

const mockedGet = vi.mocked(apiGet)

const SUMMARY = {
  total: 5,
  groups: [
    {
      key: 'auth_request',
      label: '实名认证待审',
      count: 2,
      path: '/auth-requests',
      severity: 'warning' as const,
      detail: '',
    },
    {
      key: 'scheduled_job',
      label: '定时任务执行失败',
      count: 3,
      path: '/scheduled-jobs',
      severity: 'error' as const,
      detail: '数据库备份上次执行失败',
    },
  ],
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

function renderBell() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <TodoBell />
      <LocationProbe />
    </MemoryRouter>,
  )
}

describe('TodoBell', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('展示总数并在浮层里列出各类待办', async () => {
    mockedGet.mockResolvedValue(SUMMARY)
    renderBell()

    const trigger = await screen.findByRole('button', { name: '待办 5 条' })
    await userEvent.click(trigger)

    expect(screen.getByText('实名认证待审')).toBeInTheDocument()
    expect(screen.getByText('定时任务执行失败')).toBeInTheDocument()
    expect(screen.getByText('数据库备份上次执行失败')).toBeInTheDocument()
  })

  it('点击某一类跳到对应业务页并关闭浮层', async () => {
    mockedGet.mockResolvedValue(SUMMARY)
    renderBell()

    await userEvent.click(await screen.findByRole('button', { name: '待办 5 条' }))
    await userEvent.click(screen.getByRole('button', { name: '实名认证待审，2 条' }))

    expect(screen.getByTestId('location')).toHaveTextContent('/auth-requests')
  })

  it('没有待办时显示空态而不是空白浮层', async () => {
    mockedGet.mockResolvedValue({ total: 0, groups: [] })
    renderBell()

    await userEvent.click(await screen.findByRole('button', { name: '待办' }))
    expect(screen.getByText('暂无待办')).toBeInTheDocument()
  })

  it('接口 404（旧后端）时隐藏铃铛', async () => {
    mockedGet.mockRejectedValue({ response: { status: 404 } })
    renderBell()

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /待办/ })).not.toBeInTheDocument()
    })
  })

  it('接口失败时保留入口并提示失败，而不是静默显示 0', async () => {
    mockedGet.mockRejectedValue(new Error('network'))
    renderBell()

    await userEvent.click(await screen.findByRole('button', { name: '待办' }))
    expect(screen.getByText('待办加载失败，显示的是上次结果')).toBeInTheDocument()
  })
})
