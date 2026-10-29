/**
 * 安装向导页的行为契约。
 *
 * 这一页的失败代价是「空库时进不去后台」，所以两个分支都必须钉住：
 * - 未初始化 → 出现创建表单（否则用户无处建号）；
 * - 已初始化 → 不再展示表单，只给登录入口（否则提交必然 409，看起来像坏了）。
 */

import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  return {
    default: { get, post: vi.fn(async () => ({})) },
    apiGet: get,
    apiPost: vi.fn(async () => ({})),
  }
})

import { apiGet } from '../../api/client'
import Setup from './index'

const mockedGet = vi.mocked(apiGet)

function renderSetup() {
  return render(
    <MemoryRouter initialEntries={['/setup']}>
      <Setup />
    </MemoryRouter>,
  )
}

describe('安装向导', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('未初始化时展示创建表单', async () => {
    mockedGet.mockResolvedValue({
      needs_setup: true,
      site_base_url: 'http://localhost',
      storage_driver: 'local',
    })
    renderSetup()

    expect(await screen.findByText('初始化管理后台')).toBeInTheDocument()
    expect(screen.getByLabelText('管理员账号')).toBeInTheDocument()
    expect(screen.getByLabelText('登录口令')).toBeInTheDocument()
    // local 驱动时给一句「不需要云账号」的提示，降低陌生人的心理门槛
    expect(screen.getByText(/本地存储/)).toBeInTheDocument()
  })

  it('已初始化时只给登录入口，不渲染表单', async () => {
    mockedGet.mockResolvedValue({
      needs_setup: false,
      site_base_url: 'http://localhost',
      storage_driver: 'cos',
    })
    renderSetup()

    expect(await screen.findByText('系统已完成初始化')).toBeInTheDocument()
    expect(screen.queryByLabelText('管理员账号')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '前往登录' })).toBeInTheDocument()
  })

  it('状态接口失败时给出可操作的错误提示，而不是一直转圈', async () => {
    mockedGet.mockRejectedValue(new Error('Network Error'))
    renderSetup()

    await waitFor(() => {
      expect(screen.getByText(/无法连接后端服务|初始化状态/)).toBeInTheDocument()
    })
    // 不能停在 loading：必须出现错误说明或表单
    expect(screen.queryByText('正在检查初始化状态…')).not.toBeInTheDocument()
  })
})
