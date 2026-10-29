/**
 * 个人中心的左右布局：**切走再回来，没保存的输入不能没**。
 *
 * 这条是改布局时唯一真正有风险的地方：原来用 `Tabs`，antd 默认不卸载非激活面板，
 * 所以「改昵称 → 点去修改密码 → 回来」草稿还在。改成左导航后如果按条件渲染，
 * 切走即卸载，用户敲了一半的昵称会被静默清空 —— 而且没有任何提示。
 * 现在的实现是「已访问过的面板保留挂载 + display:none」，这个文件钉住它。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
  apiGet: vi.fn(),
  apiPost: vi.fn(async () => ({})),
  apiPut: vi.fn(async () => ({})),
  apiDelete: vi.fn(async () => ({})),
}))

vi.mock('../../utils/upload', () => ({ uploadAvatar: vi.fn(async () => ({ avatar_url: '' })) }))

import { apiGet } from '../../api/client'
import ProfilePage from './index'

const get = apiGet as unknown as ReturnType<typeof vi.fn>

describe('个人中心布局', () => {
  beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue({
      id: 4,
      account: 'admin@example.com',
      role: 'admin',
      nickname: '小猫',
      bio: '',
      avatar_url: null,
      created_at: '2026-01-01T00:00:00Z',
    })
  })

  it('默认落在「账号信息」，左侧导航含五个面板', async () => {
    const { container } = render(
      <MemoryRouter>
        <ProfilePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(container.textContent || '').toContain('保存资料'))
    for (const label of ['账号信息', '修改密码', '安全设置', '我的操作记录', '我的权限']) {
      expect(container.textContent).toContain(label)
    }
  })

  it('切到别的面板再回来，未保存的昵称仍在（不是切走即卸载）', async () => {
    const { container } = render(
      <MemoryRouter>
        <ProfilePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(container.textContent || '').toContain('保存资料'))

    const nickname = container.querySelector('#nickname') as HTMLInputElement
    expect(nickname).toBeTruthy()
    fireEvent.change(nickname, { target: { value: '改了一半的昵称' } })
    expect(nickname.value).toBe('改了一半的昵称')

    // 切到「修改密码」：账号信息面板被隐藏但仍挂载
    fireEvent.click(screen.getByRole('button', { name: '修改密码' }))
    await waitFor(() => expect(container.textContent || '').toContain('确认修改'))
    expect((container.querySelector('#nickname') as HTMLInputElement).value).toBe('改了一半的昵称')

    // 再切回来
    fireEvent.click(screen.getByRole('button', { name: '账号信息' }))
    expect((container.querySelector('#nickname') as HTMLInputElement).value).toBe('改了一半的昵称')
  })

  it('未访问过的面板不发起请求（省掉无谓请求）', async () => {
    render(
      <MemoryRouter>
        <ProfilePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(get).toHaveBeenCalledWith('/users/profile'))
    // 操作记录会打 /admin/audit-logs，权限会打 /admin/rbac/profile —— 都没点过，不该请求
    const urls = get.mock.calls.map((c) => String(c[0]))
    expect(urls.some((u) => u.includes('/admin/audit-logs'))).toBe(false)
    expect(urls.some((u) => u.includes('/admin/rbac/profile'))).toBe(false)
  })
})
