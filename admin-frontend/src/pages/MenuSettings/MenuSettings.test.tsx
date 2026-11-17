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
import { useAuthStore } from '../../store/authStore'
import MenuSettings from './index'

const mockedGet = vi.mocked(apiGet)
const mockedPut = vi.mocked(apiPut)
const mockedDelete = vi.mocked(apiDelete)
const mockedPost = vi.mocked(apiPost)

const DATA = {
  custom_count: 0,
  // 顶级独立项：首页「工作台」不在任何分组里
  top: [
    {
      key: '/',
      kind: 'item',
      label: '工作台',
      label_zh: '工作台',
      label_en: 'Workplace',
      default_label_zh: '工作台',
      default_label_en: 'Workplace',
      icon: 'workplace',
      default_icon: 'workplace',
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
          key: '/dashboard',
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
          route: '/dashboard',
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
        {
          key: '/contracts',
          kind: 'item',
          label: '合同管理',
          label_zh: '合同管理',
          label_en: 'Contracts',
          default_label_zh: '合同管理',
          default_label_en: 'Contracts',
          icon: 'file-text',
          default_icon: 'file-text',
          visible: false,
          sort_order: 1,
          is_custom: true,
          route: '/contracts',
          perm: 'contract:view',
          perms: [],
          url: null,
          module_enabled: true,
        },
      ],
    },
  ],
}

/** 默认折叠，展开全部分组后才能看到叶子行 */
async function expandAll() {
  await screen.findByText('概览')
  await userEvent.click(screen.getByRole('button', { name: /展开全部/ }))
}

describe('MenuSettings', () => {
  beforeEach(() => {
    useAuthStore.setState({ isSuper: true, permissions: [] })
    mockedGet.mockReset()
    mockedPut.mockReset()
    mockedDelete.mockReset()
    mockedPost.mockReset()
    mockedGet.mockResolvedValue(DATA)
    mockedPut.mockResolvedValue({ ok: true })
    mockedDelete.mockResolvedValue({ ok: true })
    mockedPost.mockResolvedValue({ reset: 0 })
  })

  it('默认折叠，展开全部后渲染菜单树、路由与权限，并说明不提供新建/删除', async () => {
    render(<MenuSettings />)
    await screen.findByText('概览')
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument()

    await expandAll()
    expect(await screen.findByText('数据看板')).toBeInTheDocument()
    expect(screen.getAllByText(/dashboard:view/).length).toBeGreaterThan(0)
    // 目标截图的列结构：序号 / 菜单名称 / 类型 / 菜单标题 / 权限标识 / 状态 / 排序 / 路由名称 / 路由路径 / 操作
    for (const title of ['序号', '菜单名称', '类型', '菜单标题', '权限标识', '状态', '排序', '路由名称', '路由路径', '操作']) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0)
    }
    // 「新增子菜单」保留入口但不可用：接口契约不提供新建
    expect(screen.getAllByRole('button', { name: /新增子菜单/ })[0]).toBeDisabled()
    expect(mockedGet).toHaveBeenCalledWith('/admin/menu-settings')
  })

  it('工作台是顶级首页项，排在概览之上且不在概览里', async () => {
    render(<MenuSettings />)
    await screen.findByText('工作台')
    // 折叠态就能看到：证明它不是「概览」的子项
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument()

    const topRow = screen.getByText('工作台').closest('tr') as HTMLElement
    const overviewRow = screen.getByText('概览').closest('tr') as HTMLElement
    // 顺序：工作台在概览上方（侧栏第一项）
    expect(
      topRow.compareDocumentPosition(overviewRow) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    // 顶级项是叶子，不能像分组那样展开
    expect(within(topRow).queryByRole('button', { name: /展开/ })).toBeNull()
  })

  it('编辑中文名称会调用覆盖接口并刷新', async () => {
    render(<MenuSettings />)
    await expandAll()
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
        key: '/dashboard',
        label_zh: '首页看板',
      })
    })
    expect(mockedGet).toHaveBeenCalledTimes(2)
  })

  it('切换可见性会保存并刷新', async () => {
    render(<MenuSettings />)
    await expandAll()
    await userEvent.click(screen.getByRole('switch', { name: '数据看板 是否可见' }))

    await waitFor(() => {
      expect(mockedPut).toHaveBeenCalledWith('/admin/menu-settings/item', {
        key: '/dashboard',
        visible: false,
      })
    })
  })

  it('搜索只保留匹配的菜单，清空后恢复', async () => {
    render(<MenuSettings />)
    await expandAll()
    const search = screen.getByPlaceholderText('请输入菜单名称')
    await userEvent.type(search, '系统')
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument()
    expect(screen.getAllByText('菜单管理').length).toBeGreaterThan(0)
    await userEvent.clear(search)
    // 清除筛选后回到折叠态，再展开即可看到全部菜单
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument()
    await expandAll()
    expect(await screen.findByText('数据看板')).toBeInTheDocument()
  })

  it('按状态筛选只保留隐藏项，重置后清空筛选', async () => {
    render(<MenuSettings />)
    await screen.findByText('概览')
    await userEvent.click(screen.getByText('全部状态'))
    // 状态开关的「停用」文字也会出现在表格里，这里只点下拉选项
    await userEvent.click(await screen.findByText('停用', { selector: '.ant-select-item-option-content' }))

    expect(await screen.findByText('合同管理')).toBeInTheDocument()
    expect(screen.queryByText('数据看板')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /重\s*置/ }))
    // 重置后筛选清空并回到折叠态，展开后全部菜单重新可见（也证明状态筛选已清空）
    expect(screen.queryByText('合同管理')).not.toBeInTheDocument()
    await expandAll()
    expect(await screen.findByText('数据看板')).toBeInTheDocument()
    expect(screen.getByText('合同管理')).toBeInTheDocument()
  })

  it('没有 setting:update 权限时只读', async () => {
    useAuthStore.setState({ isSuper: false, permissions: ['setting:view'] })
    render(<MenuSettings />)
    await expandAll()
    await screen.findByText('数据看板')
    expect(screen.getByRole('button', { name: /编辑 数据看板/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /全部恢复默认/ })).not.toBeInTheDocument()
    expect(screen.getByRole('switch', { name: '数据看板 是否可见' })).toBeDisabled()
  })

  it('查看详情打开只读抽屉，展示真实字段', async () => {
    render(<MenuSettings />)
    await expandAll()
    await screen.findByText('数据看板')
    await userEvent.click(screen.getByRole('button', { name: '查看 数据看板' }))

    expect(await screen.findByText('菜单详情 - 数据看板')).toBeInTheDocument()
    expect(screen.getByText('代码默认英文名')).toBeInTheDocument()
    expect(screen.getByText('启用（显示中）')).toBeInTheDocument()
    expect(screen.getByText('使用代码默认值')).toBeInTheDocument()
  })

  it('更多菜单里的「恢复默认」走二次确认，确认后删除覆盖', async () => {
    render(<MenuSettings />)
    await expandAll()
    await screen.findByText('合同管理')
    await userEvent.click(screen.getByRole('button', { name: '更多操作 合同管理' }))
    await userEvent.click(await screen.findByText('恢复默认（删除覆盖）'))

    expect((await screen.findAllByText('恢复「合同管理」的默认显示？')).length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole('button', { name: '恢复默认' }))

    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith('/admin/menu-settings/item', {
        params: { key: '/contracts' },
      })
    })
  })
})
