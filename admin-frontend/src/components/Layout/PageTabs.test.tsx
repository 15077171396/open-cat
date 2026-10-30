import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import PageTabs from './PageTabs'

const titles: Record<string, string> = { '/': '首页', '/users': '用户管理' }

function renderTabs(overrides: Partial<React.ComponentProps<typeof PageTabs>> = {}) {
  const props = {
    tabs: ['/', '/users'],
    activeKey: '/users',
    getTitle: (key: string) => titles[key] ?? key,
    onChange: vi.fn(),
    onClose: vi.fn(),
    onCloseOthers: vi.fn(),
    onCloseAll: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  }
  render(<PageTabs {...props} />)
  return props
}

describe('PageTabs', () => {
  it('渲染全部标签并标记当前项', () => {
    renderTabs()
    const active = screen.getByRole('tab', { name: /用户管理/ })
    expect(active).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /首页/ })).toHaveAttribute('aria-selected', 'false')
  })

  it('点击标签切换路由', async () => {
    const props = renderTabs()
    await userEvent.click(screen.getByRole('button', { name: '首页' }))
    expect(props.onChange).toHaveBeenCalledWith('/')
  })

  it('点击关闭只触发关闭，不触发切换', async () => {
    const props = renderTabs()
    await userEvent.click(screen.getByRole('button', { name: '关闭 用户管理' }))
    expect(props.onClose).toHaveBeenCalledWith('/users')
    expect(props.onChange).not.toHaveBeenCalled()
  })

  it('首页不可关闭', () => {
    renderTabs()
    expect(screen.queryByRole('button', { name: '关闭 首页' })).not.toBeInTheDocument()
  })

  it('刷新按钮触发 onRefresh', async () => {
    const props = renderTabs()
    await userEvent.click(screen.getByRole('button', { name: '刷新当前页' }))
    expect(props.onRefresh).toHaveBeenCalledTimes(1)
  })
})
