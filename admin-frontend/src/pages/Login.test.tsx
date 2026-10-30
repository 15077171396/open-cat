/**
 * 登录页 i18n 契约：
 * 1. 默认中文（老用户零变化）；
 * 2. 登录页可切语言（后台外壳之外的唯一语言入口），切换后校验文案与按钮同步；
 * 3. 选择持久化在 `prefsStore`，进后台后沿用。
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  apiGet: vi.fn().mockResolvedValue({ needs_setup: false }),
}))

import Login from './Login'
import { usePrefsStore } from '../store/prefsStore'

function renderLogin() {
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>,
  )
}

describe('Login 语言切换', () => {
  beforeEach(() => {
    usePrefsStore.setState({ locale: 'zh-CN' })
  })

  afterEach(() => {
    usePrefsStore.setState({ locale: 'zh-CN' })
  })

  it('默认中文：标题、按钮与必填提示都是中文', async () => {
    renderLogin()
    expect(screen.getByText('AI学习猫 管理后台')).toBeInTheDocument()
    // antd 会给两个汉字之间自动加空格（登 录），按可访问名用正则匹配
    expect(screen.getByRole('button', { name: /登\s*录/ })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /登\s*录/ }))
    expect(await screen.findByText('请输入手机号或邮箱')).toBeInTheDocument()
  })

  it('测试模式（同生产）打开登录页不预填账号密码', () => {
    renderLogin()
    expect(screen.getByPlaceholderText('手机号 / 邮箱')).toHaveValue('')
    expect(screen.getByPlaceholderText('请输入密码')).toHaveValue('')
  })

  it('切到 English 后整页文案跟随，并持久化选择', async () => {
    renderLogin()
    await userEvent.click(screen.getByText('English'))

    expect(usePrefsStore.getState().locale).toBe('en-US')
    expect(screen.getByText('AI Cat Admin')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Enter your phone number or email')).toBeInTheDocument()
  })
})
