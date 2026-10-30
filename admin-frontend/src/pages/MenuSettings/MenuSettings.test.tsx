import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  const put = vi.fn()
  const del = vi.fn()
  const post = vi.fn()
  return { apiGet: get, apiPut: put, apiDelete: del, apiPost: post }
})

import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import MenuSettings from './index'

const mockedGet = vi.mocked(apiGet)
const mockedPut = vi.mocked(apiPut)
const mockedDelete = vi.mocked(apiDelete)
const mockedPost = vi.mocked(apiPost)

const DATA = {
  custom_count: 0,
  footer: [
    {
      key: '/profile',
      kind: 'footer',
      label: '个人中心',
      label_zh: '个人中心',
      label_en: 'Profile',
      default_label_zh: '个人中心',
      default_label_en: 'Profile',
      icon: 'user',
      default_icon: 'user',
      visible: true,
      sort_order: 0,
      is_custom: false,
      route: '/profile',
      perm: null,
      perms: [],
      url: null,
      module_enabled: true,
    },
  ],
  groups: [
    {
      key: 'overview',
      kind: 'group',
      label: '概览',
      label_zh: '概览',
      label_en: 'Overview',
      default_label_zh: '概览',
      default_label_en: 'Overview',
      icon: 'home',
      default_icon: 'home',
      visible: true,
      sort_order: 0,
      is_custom: false,
      route: null,
      perm: null,
      perms: [],
      url: null,
      module_enabled: true,
      items: [
        {
          key: '/',
          kind: 'item',
          label: '数据看板',
          label_zh: '数据看板',
          label_en: 'Dashboard',
          default_label_zh: '数据看板',
          default_label_en: 'Dashboard',
          icon: 'dashboard',
          default_icon: 'dashboard',
          visible: true,
          sort_order: 0,
          is_custom: false,
          route: '/',
          perm: 'dashboard:view',
          perms: [],
          url: null,
          module_enabled: true,
        },
      ],
    },
    {
      key: 'system',
      kind: 'group',
      label: '系统',
      label_zh: '系统',
      label_en: 'System',
      default_label_zh: '系统',
      default_label_en: 'System',
      icon: 'control',
      default_icon: 'control',
      visible: true,
      sort_order: 1,
      is_custom: false,
      route: null,
      perm: null,
      perms: [],
      url: null,
      module_enabled: true,
      items: [
        {
          key: '/menu-settings',
          kind: 'item',
          label: '菜单管理',
          label_zh: '菜单管理',
          label_en: 'Menu Management',
          default_label_zh: '菜单管理',
          default_label_en: 'Menu Management',
          icon: 'menu',
          default_icon: 'menu',
          visible: true,
          sort_order: 0,
          is_custom: false,
          route: '/menu-settings',
          perm: 'setting:view',
          perms: [],
          url: null,
          module_enabled: true,
        },
      ],
    },
  ],
}

describe('MenuSettings', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedPut.mockReset()
    mockedDelete.mockReset()
    mockedPost.mockReset()
    mockedGet.mockResolvedValue(DATA)
    mockedPut.mockResolvedValue({ ok: true })
    mockedDelete.mockResolvedValue({ ok: true })
    mockedPost.mockResolvedValue({ reset: 0 })
  })

  it('渲染菜单树、路由与权限，并说明不提供新建/删除', async () => {
    render(<MenuSettings />)
    expect((await screen.findAllByText('菜单管理')).length).toBeGreaterThan(0)
    expect(screen.getByText('数据看板')).toBeInTheDocument()
    expect(screen.getAllByText(/dashboard:view/).length).toBeGreaterThan(0)
    expect(screen.getByText('菜单不会凭空新增或删除')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /新建/ })).not.toBeInTheDocument()
    expect(mockedGet).toHaveBeenCalledWith('/admin/menu-settings')
  })

  it('编辑中文名称会调用覆盖接口并刷新', async () => {
    render(<MenuSettings />)
    await screen.findByText('数据看板')
    const row = screen.getByText('数据看板').closest('tr')
    expect(row).not.toBeNull()
    await userEvent.click(within(row as HTMLElement).getByRole('button', { name: /编辑/ }))

    const input = await screen.findByLabelText('中文名称')
    await userEvent.clear(input)
    await userEvent.type(input, '首页看板')
    await userEvent.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => {
      expect(mockedPut).toHaveBeenCalledWith('/admin/menu-settings/item', {
        key: '/',
        label_zh: '首页看板',
      })
    })
    expect(mockedGet).toHaveBeenCalledTimes(2)
  })

  it('切换可见性会保存并刷新', async () => {
    render(<MenuSettings />)
    await screen.findByText('数据看板')
    await userEvent.click(screen.getByRole('switch', { name: '数据看板 是否可见' }))

    await waitFor(() => {
      expect(mockedPut).toHaveBeenCalledWith('/admin/menu-settings/item', {
        key: '/',
        visible: false,
      })
    })
  })

  it('搜索只保留匹配的菜单，清空后恢复', async () => {
    render(<MenuSettings />)
    await screen.findByText('数据看板')
    const search = screen.getByPlaceholderText('搜索名称 / 路由 / 权限码')
    await userEvent.type(search, '系统')
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument()
    expect(screen.getAllByText('菜单管理').length).toBeGreaterThan(0)
    await userEvent.clear(search)
    expect(await screen.findByText('数据看板')).toBeInTheDocument()
  })
})
