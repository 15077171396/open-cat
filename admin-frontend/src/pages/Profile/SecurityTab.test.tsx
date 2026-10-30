/**
 * 管理员强制 2FA 的 UI 契约：强制开启时不能从个人中心关闭第二因子，
 * 否则「服务端拒绝 + 前端按钮可点」会变成一次无意义的失败操作。
 */

import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  return { apiGet: get, apiDelete: vi.fn(), apiPost: vi.fn() }
})

import { apiGet } from '../../api/client'
import SecurityTab from './SecurityTab'

const mockedGet = vi.mocked(apiGet)

function mockStatus(status: Record<string, unknown>) {
  mockedGet.mockImplementation((url: string) => {
    if (url === '/auth/totp') return Promise.resolve(status)
    if (url === '/admin/sessions') return Promise.resolve({ items: [], total: 0 })
    return Promise.resolve({})
  })
}

describe('SecurityTab 强制 2FA', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('required=true 时显示「管理员强制」并禁用关闭按钮', async () => {
    mockStatus({
      enabled: true,
      pending: false,
      recovery_remaining: 8,
      recovery_should_regenerate: false,
      required: true,
    })
    render(<SecurityTab />)

    expect(await screen.findByText('管理员强制')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /关闭二次验证/ })).toBeDisabled()
  })

  it('required=false 时允许正常关闭', async () => {
    mockStatus({
      enabled: true,
      pending: false,
      recovery_remaining: 8,
      recovery_should_regenerate: false,
      required: false,
    })
    render(<SecurityTab />)

    expect(await screen.findByText('已启用')).toBeInTheDocument()
    expect(screen.queryByText('管理员强制')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /关闭二次验证/ })).toBeEnabled()
  })
})
