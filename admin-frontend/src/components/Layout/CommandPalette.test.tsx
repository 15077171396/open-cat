/**
 * 命令面板的契约：
 * 1. Ctrl/Cmd+K 在任意页面都能唤起；
 * 2. 页面跳转复用可见菜单树，不自己维护一份路由；
 * 3. 教程 / 用户搜索落到对应页面（用户带 ?keyword= 预填）；
 * 4. 没有权限的快捷动作不出现。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  return { apiGet: get }
})

import { apiGet } from '../../api/client'
import CommandPalette from './CommandPalette'
import { usePrefsStore } from '../../store/prefsStore'
import type { MenuTree } from '../../types'

const mockedGet = vi.mocked(apiGet)

const MENU: MenuTree = {
  groups: [
    {
      key: 'overview',
      label: '概览',
      icon: 'home',
      items: [{ key: '/', label: '数据看板', icon: 'dashboard' }],
    },
    {
      key: 'user',
      label: '用户与权限',
      icon: 'idcard',
      items: [{ key: '/users', label: '用户管理', icon: 'team' }],
    },
  ],
  footer: [{ key: '/profile', label: '个人中心', icon: 'user' }],
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

function Harness({
  permissions = ['tutorial:view', 'tutorial:create'],
  isSuper = false,
  initialOpen = false,
}: {
  permissions?: string[]
  isSuper?: boolean
  initialOpen?: boolean
}) {
  const [open, setOpen] = useState(initialOpen)
  return (
    <MemoryRouter initialEntries={['/']}>
      <CommandPalette
        open={open}
        onOpenChange={setOpen}
        menuTree={MENU}
        permissions={permissions}
        isSuper={isSuper}
      />
      <LocationProbe />
    </MemoryRouter>
  )
}

describe('CommandPalette', () => {
  beforeEach(() => {
    usePrefsStore.setState({ locale: 'zh-CN' })
    mockedGet.mockReset()
    mockedGet.mockImplementation((url: string) => {
      if (url === '/admin/users') {
        return Promise.resolve({
          items: [{ id: 7, account: 'alice@test', role: 'user' }],
          total: 1,
        })
      }
      if (url === '/admin/tutorials') {
        return Promise.resolve({
          items: [{ id: 9, title: 'ComfyUI 入门', category: 'AI' }],
          total: 1,
        })
      }
      return Promise.resolve({ items: [], total: 0 })
    })
  })

  afterEach(() => {
    usePrefsStore.setState({ locale: 'zh-CN' })
  })

  it('Ctrl+K 能唤起，页面项可直接跳转', async () => {
    render(<Harness />)
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })

    const input = await screen.findByPlaceholderText('搜索页面、教程、用户，或输入操作…')
    await userEvent.type(input, '用户管理')
    await userEvent.type(input, '{Enter}')

    expect(screen.getByTestId('location')).toHaveTextContent('/users')
  })

  it('快捷动作受权限控制：有 tutorial:create 才出现「新建教程」', async () => {
    render(<Harness initialOpen permissions={['tutorial:view']} />)
    const input = await screen.findByPlaceholderText('搜索页面、教程、用户，或输入操作…')
    await userEvent.type(input, '新建教程')
    expect(screen.queryByText('新建教程')).not.toBeInTheDocument()

    // 重新渲染一份有权限的实例（同一测试里不销毁第一个，故用独立断言）
    expect(screen.queryByRole('button', { name: /新建教程/ })).not.toBeInTheDocument()
  })

  it('有 tutorial:create 时快捷动作可直接新建教程', async () => {
    render(<Harness initialOpen permissions={['tutorial:create']} />)
    const input = await screen.findByPlaceholderText('搜索页面、教程、用户，或输入操作…')
    await userEvent.type(input, '新建教程')
    await userEvent.type(input, '{Enter}')

    expect(screen.getByTestId('location')).toHaveTextContent('/tutorials/new')
  })

  it('搜索用户并落到用户列表的关键词查询', async () => {
    render(<Harness initialOpen permissions={['tutorial:view', 'user:view']} />)
    const input = await screen.findByPlaceholderText('搜索页面、教程、用户，或输入操作…')
    await userEvent.type(input, 'alice')

    const userResult = await screen.findByText('alice@test')
    await userEvent.click(userResult)

    await waitFor(() => {
      const location = screen.getByTestId('location').textContent || ''
      expect(location).toContain('/users?keyword=')
      expect(location).toContain('alice')
    })
  })

  it('搜索教程时可直接进入编辑页（有 tutorial:edit 时）', async () => {
    render(<Harness initialOpen permissions={['tutorial:view', 'tutorial:edit']} />)
    const input = await screen.findByPlaceholderText('搜索页面、教程、用户，或输入操作…')
    await userEvent.type(input, 'Comfy')

    await screen.findByText('ComfyUI 入门')
    await userEvent.type(input, '{Enter}')

    expect(screen.getByTestId('location')).toHaveTextContent('/tutorials/9/edit')
  })

  it('可直接切换界面语言，文案即时跟随', async () => {
    render(<Harness initialOpen />)
    const input = await screen.findByPlaceholderText('搜索页面、教程、用户，或输入操作…')
    await userEvent.type(input, 'English')

    await userEvent.click(await screen.findByRole('button', { name: /切换到 English/ }))

    // 执行任意命令都会关闭面板（与跳转类命令一致），重新唤起后检查语言已切换
    expect(usePrefsStore.getState().locale).toBe('en-US')
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })

    expect(
      await screen.findByPlaceholderText('Search pages, tutorials, users or actions…'),
    ).toBeInTheDocument()
    expect(screen.getByText('Quick actions')).toBeInTheDocument()
  })

  it('Esc 关闭面板', async () => {
    render(<Harness initialOpen />)
    await screen.findByPlaceholderText('搜索页面、教程、用户，或输入操作…')
    fireEvent.keyDown(window, { key: 'Escape' })

    await waitFor(() => {
      expect(screen.queryByPlaceholderText('搜索页面、教程、用户，或输入操作…')).not.toBeInTheDocument()
    })
  })
})
