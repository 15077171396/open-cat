/**
 * 登录页图形验证码契约：
 * 1. 能力位（/auth/capabilities 的 captcha_enabled）决定是否渲染验证码框；
 * 2. 渲染时先取图（避免用户提交后才被打回），点图可刷新；
 * 3. 开关未开启时零 UI 变化（存量登录页不受影响）。
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGetMock = vi.fn()

vi.mock('../api/client', () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}))

import Login from './Login'

const CAPTCHA_RESPONSE = {
  captcha_id: 'cap-1',
  svg: '<svg xmlns="http://www.w3.org/2000/svg"><text>AB2D</text></svg>',
  expires_in: 300,
}

function mockCapabilities(enabled: boolean) {
  apiGetMock.mockImplementation((url: string) => {
    if (url.includes('setup/status')) return Promise.resolve({ needs_setup: false })
    if (url.includes('capabilities')) return Promise.resolve({ captcha_enabled: enabled })
    if (url.includes('captcha')) return Promise.resolve({ ...CAPTCHA_RESPONSE })
    return Promise.resolve({})
  })
}

function renderLogin() {
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>,
  )
}

describe('Login 图形验证码', () => {
  beforeEach(() => {
    apiGetMock.mockReset()
  })

  it('开启时渲染验证码输入框并先取图', async () => {
    mockCapabilities(true)
    renderLogin()
    expect(await screen.findByPlaceholderText('输入图中字符')).toBeInTheDocument()
    expect(screen.getByAltText('图形验证码')).toHaveAttribute('src', expect.stringContaining('data:image/svg+xml'))
    // 进页面就取图：不让用户提交后才被打回
    expect(apiGetMock).toHaveBeenCalledWith('/auth/captcha')
  })

  it('点击验证码图会刷新（一次性消费后必须换新图）', async () => {
    mockCapabilities(true)
    renderLogin()
    await screen.findByAltText('图形验证码')
    await userEvent.click(screen.getByAltText('图形验证码'))
    // 只数验证码端点本身（effects 在测试环境可能因 StrictMode 双跑，计数不稳定）
    const captchaCalls = apiGetMock.mock.calls.filter(([url]) => String(url).includes('captcha'))
    expect(captchaCalls.length).toBeGreaterThanOrEqual(2)
  })

  it('未开启时不渲染验证码框（存量登录页零变化）', async () => {
    mockCapabilities(false)
    renderLogin()
    // 等能力位请求落地后再断言
    await screen.findByPlaceholderText('手机号 / 邮箱')
    expect(screen.queryByPlaceholderText('输入图中字符')).not.toBeInTheDocument()
    expect(apiGetMock).not.toHaveBeenCalledWith('/auth/captcha')
  })
})
