import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Key, ReactNode } from 'react'
import type { MenuProps, TableProps } from 'antd'
import {
  Button,
  Descriptions,
  Drawer,
  Dropdown,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
} from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DownOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  MenuFoldOutlined,
  MenuOutlined,
  MenuUnfoldOutlined,
  MoreOutlined,
  PlusOutlined,
  ReloadOutlined,
  RightOutlined,
  SearchOutlined,
  UndoOutlined,
} from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { DataTable, ListToolbar, ModalForm, PageHeader } from '../../components/ui'
import { PermGuard } from '../../components/PermGuard'
import { MENU_ICON_NAMES, menuIcon } from '../../constants/menuIcons'
import { useAuthStore } from '../../store/authStore'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'

interface MenuEntry {
  key: string
  kind: 'group' | 'item' | 'footer'
  label: string
  label_zh: string
  label_en: string
  default_label_zh: string
  default_label_en: string
  icon: string
  default_icon: string
  visible: boolean
  sort_order: number
  is_custom: boolean
  route: string | null
  perm: string | null
  perms: string[]
  url: string | null
  module_enabled: boolean
  items?: MenuEntry[]
}

interface MenuSettingsData {
  /** 分组之上的顶级独立项（如首页「工作台」） */
  top: MenuEntry[]
  groups: MenuEntry[]
  footer: MenuEntry[]
  custom_count: number
}

interface Row extends MenuEntry {
  parentKey?: string
  children?: Row[]
}

interface MenuFormValues {
  label_zh?: string
  label_en?: string
  icon?: string
}

type TypeFilter = '' | 'group' | 'item' | 'footer'
type StatusFilter = '' | 'visible' | 'hidden'

const FOOTER_GROUP_KEY = '__footer__'
/** 顶层分页：接口整树返回，分页只在根节点层做（当前 8 个根节点，不足一页不显示分页条） */
const PAGE_SIZE = 20

const TYPE_FILTER_OPTIONS = [
  { value: '', label: '全部类型' },
  { value: 'group', label: '目录' },
  { value: 'item', label: '菜单' },
  { value: 'footer', label: '页脚项' },
]

const STATUS_FILTER_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'visible', label: '启用' },
  { value: 'hidden', label: '停用' },
]

/** 类型列文案：分组即目录，与演示站「目录 / 菜单 / 按钮」的层级语义对齐 */
const TYPE_LABELS: Record<Row['kind'], string> = {
  group: '目录',
  item: '菜单',
  footer: '页脚',
}

function permList(entry: MenuEntry): string[] {
  const list = [entry.perm, ...entry.perms].filter((value): value is string => Boolean(value))
  return [...new Set(list)]
}

function matches(entry: MenuEntry, query: string): boolean {
  const haystack = [
    entry.label,
    entry.label_zh,
    entry.label_en,
    entry.key,
    entry.route,
    entry.perm,
    ...entry.perms,
  ]
  return haystack.some((value) => String(value ?? '').toLowerCase().includes(query))
}

interface IconActionProps {
  /** 无障碍名称（读屏与测试用） */
  label: string
  icon: ReactNode
  /** 悬停说明（禁用按钮也可见） */
  tip: string
  disabled?: boolean
  onClick?: () => void
}

/** 行内图标按钮：Tooltip 包一层 span，禁用态也能显示说明 */
function IconAction({ label, icon, tip, disabled, onClick }: IconActionProps) {
  return (
    <Tooltip title={tip}>
      <span className="ms-action">
        <Button
          type="text"
          size="small"
          aria-label={label}
          icon={icon}
          disabled={disabled}
          onClick={onClick}
        />
      </span>
    </Tooltip>
  )
}

/**
 * 菜单管理（展示层）。
 *
 * 我们的菜单 / 路由 / 权限码是**代码同源**的（路由表守卫会在 CI 里比对三者），
 * 所以这里刻意不提供新建/删除：凭空加一条菜单只会得到点进去 404 或 403 的死入口。
 * 页面负责调整名称、图标、顺序、可见性；数据存 DB 覆盖，清空即回到代码默认。
 *
 * 版式对齐 FastapiAdmin 的「系统管理 → 菜单管理」：
 * 列 = 序号 / 菜单名称 / 类型 / 菜单标题 / 权限标识 / 状态 / 排序 / 路由名称 / 路由路径 / 操作，
 * 行内 = 新增子菜单（置灰说明）/ 查看 / 编辑 / 更多（恢复默认，等同删除覆盖）。
 * 演示站有而本项目接口没有的「终端、创建/更新时间、APP 菜单页签、批量删除」一律隐藏，
 * 见交付说明；不造假数据。
 */
export default function MenuSettings() {
  const { isSuper, permissions } = useAuthStore()
  const canUpdate = isSuper || (permissions?.includes('setting:update') ?? false)

  const [data, setData] = useState<MenuSettingsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('')
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])
  const [editing, setEditing] = useState<Row | null>(null)
  const [viewing, setViewing] = useState<Row | null>(null)
  const [page, setPage] = useState(1)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await apiGet<MenuSettingsData>('/admin/menu-settings')
      setData(result)
    } catch (error) {
      toast.error(getErrorMessage(error, '菜单加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const rows = useMemo<Row[]>(() => {
    if (!data) return []
    // 顶级独立项没有父级，直接作为根行展示（排在分组之上，与侧栏顺序一致）
    const list: Row[] = data.top.map((item) => ({ ...item }))
    for (const group of data.groups) {
      list.push({
        ...group,
        children: (group.items ?? []).map((item) => ({ ...item, parentKey: group.key })),
      })
    }
    if (data.footer.length > 0) {
      list.push({
        key: FOOTER_GROUP_KEY,
        kind: 'group',
        label: '页脚',
        label_zh: '页脚',
        label_en: 'Footer',
        default_label_zh: '页脚',
        default_label_en: 'Footer',
        icon: 'user',
        default_icon: 'user',
        visible: true,
        sort_order: 9999,
        is_custom: false,
        route: null,
        perm: null,
        perms: [],
        url: null,
        module_enabled: true,
        children: data.footer.map((item) => ({ ...item, parentKey: FOOTER_GROUP_KEY })),
      })
    }
    return list
  }, [data])

  const hasFilter = query.trim().length > 0 || typeFilter !== '' || statusFilter !== ''

  const filteredRows = useMemo<Row[]>(() => {
    const keyword = query.trim().toLowerCase()
    const keywordOk = (entry: MenuEntry) => !keyword || matches(entry, keyword)
    const statusOk = (entry: MenuEntry) =>
      statusFilter === '' ? true : statusFilter === 'visible' ? entry.visible : !entry.visible
    const typeOk = (entry: MenuEntry) =>
      typeFilter === '' ||
      (entry.kind === 'group' && typeFilter === 'group') ||
      entry.kind === typeFilter

    const result: Row[] = []
    for (const group of rows) {
      // 顶级独立项是根级叶子（没有子项），自身就是筛选目标，不走「分组容器」逻辑
      if ((group.children?.length ?? 0) === 0) {
        if (keywordOk(group) && statusOk(group) && typeOk(group)) result.push({ ...group })
        continue
      }
      const groupKeywordOk = keywordOk(group)
      const groupSelf = groupKeywordOk && statusOk(group) && typeOk(group)
      const children = (group.children ?? []).filter(
        (child) => keywordOk(child) && statusOk(child) && typeOk(child),
      )

      if (typeFilter === 'group') {
        // 只看目录：不再展开子项，目录本身就是筛选目标
        if (groupSelf) result.push({ ...group, children: [] })
        continue
      }
      if (typeFilter === 'item' || typeFilter === 'footer') {
        // 只按子项筛选：目录只作为容器出现，自身不单独命中
        if (children.length > 0) result.push({ ...group, children })
        continue
      }
      if (groupSelf) {
        // 目录命中时保留全部子项作为上下文
        result.push({
          ...group,
          children: (group.children ?? []).filter((child) => statusOk(child) && typeOk(child)),
        })
      } else if (children.length > 0) {
        result.push({ ...group, children })
      }
    }
    return result
  }, [rows, query, typeFilter, statusFilter])

  // 序号：整棵树的显示顺序（折叠时子项不渲染，但编号保持稳定，与演示站的序号列语义一致）
  const sequenceMap = useMemo(() => {
    const map = new Map<string, number>()
    let index = 1
    const walk = (list: Row[]) => {
      for (const item of list) {
        map.set(item.key, index)
        index += 1
        if (item.children?.length) walk(item.children)
      }
    }
    walk(filteredRows)
    return map
  }, [filteredRows])

  // 筛选时自动展开命中的目录，否则树形表格里只剩父级、看不到筛选结果；
  // 只在「筛选 → 取消筛选」的切换点收回展开，避免刷新/编辑后把用户手动展开的目录全部折叠
  const wasFiltering = useRef(false)
  useEffect(() => {
    if (hasFilter) {
      setExpandedKeys(filteredRows.map((row) => row.key))
    } else if (wasFiltering.current) {
      setExpandedKeys([])
    }
    wasFiltering.current = hasFilter
  }, [hasFilter, filteredRows])

  const expandableKeys = useMemo(
    () => filteredRows.filter((row) => (row.children?.length ?? 0) > 0).map((row) => row.key),
    [filteredRows],
  )
  const allExpanded =
    expandableKeys.length > 0 && expandableKeys.every((key) => expandedKeys.includes(key))

  const toggleAll = () => {
    setExpandedKeys(allExpanded ? [] : expandableKeys)
  }

  const resetFilters = () => {
    setQuery('')
    setTypeFilter('')
    setStatusFilter('')
    setPage(1)
    // 筛选时的自动展开一并收回，回到默认的折叠态，避免重置后仍停留在「筛选期」的展开结果
    setExpandedKeys([])
  }

  const refresh = useCallback(async () => {
    await load()
    // AdminLayout 监听此事件重拉菜单，保存后侧栏立即反映新名称/顺序/可见性
    window.dispatchEvent(new Event('admin-menu-updated'))
  }, [load])

  const siblingsOf = useCallback(
    (record: Row): MenuEntry[] => {
      if (!data) return []
      if (record.kind === 'group') return data.groups
      if (record.parentKey === FOOTER_GROUP_KEY) return data.footer
      // 顶级独立项：同级只有 top 列表，排序仅影响它自己的顺序
      if (!record.parentKey) return data.top
      return data.groups.find((group) => group.key === record.parentKey)?.items ?? []
    },
    [data],
  )

  const move = async (record: Row, direction: -1 | 1) => {
    const siblings = siblingsOf(record)
    const index = siblings.findIndex((item) => item.key === record.key)
    const target = index + direction
    if (index < 0 || target < 0 || target >= siblings.length) return
    const ordered = [...siblings]
    ;[ordered[index], ordered[target]] = [ordered[target], ordered[index]]
    try {
      await apiPut('/admin/menu-settings/order', {
        items: ordered.map((item, position) => ({ key: item.key, sort_order: position })),
      })
      await refresh()
    } catch (error) {
      toast.error(getErrorMessage(error, '排序保存失败'))
    }
  }

  const updateVisible = async (record: Row, visible: boolean) => {
    try {
      await apiPut('/admin/menu-settings/item', { key: record.key, visible })
      if (record.key === '/menu-settings' && !visible) {
        toast.warning('已隐藏本页入口；需要直接访问 /admin/menu-settings 才能回来')
      }
      await refresh()
    } catch (error) {
      toast.error(getErrorMessage(error, '可见性保存失败'))
    }
  }

  const resetOne = async (record: Row) => {
    try {
      await apiDelete('/admin/menu-settings/item', { params: { key: record.key } })
      toast.success('已恢复代码默认值')
      await refresh()
    } catch (error) {
      toast.error(getErrorMessage(error, '恢复失败'))
    }
  }

  const confirmResetOne = (record: Row) => {
    Modal.confirm({
      title: `恢复「${record.label}」的默认显示？`,
      content:
        '相当于删除这条菜单的自定义显示设置：将清除中文名、英文名、图标、显示状态与排序覆盖；菜单本身、路由与权限码不受影响。',
      okText: '恢复默认',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => resetOne(record),
    })
  }

  const resetAll = async () => {
    try {
      const result = await apiPost<{ reset: number }>('/admin/menu-settings/reset')
      toast.success(result.reset > 0 ? `已恢复 ${result.reset} 项默认值` : '没有需要恢复的项')
      await refresh()
    } catch (error) {
      toast.error(getErrorMessage(error, '恢复失败'))
    }
  }

  const confirmResetAll = () => {
    Modal.confirm({
      title: '恢复全部菜单默认显示？',
      content: `将清除 ${data?.custom_count ?? 0} 项自定义名称、图标、显示状态与排序覆盖；路由与权限码不受影响。`,
      okText: '恢复默认',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => resetAll(),
    })
  }

  const submitEdit = async (values: MenuFormValues) => {
    if (!editing) return
    const payload: Record<string, string | null> = {}
    const nextZh = (values.label_zh ?? '').trim()
    const nextEn = (values.label_en ?? '').trim()
    const nextIcon = (values.icon ?? '').trim()
    if (nextZh !== editing.label_zh) payload.label_zh = nextZh || null
    if (nextEn !== editing.label_en) payload.label_en = nextEn || null
    if (nextIcon !== editing.icon) payload.icon = nextIcon || null
    if (Object.keys(payload).length === 0) {
      setEditing(null)
      return
    }
    try {
      await apiPut('/admin/menu-settings/item', { key: editing.key, ...payload })
      toast.success('菜单已更新')
      setEditing(null)
      await refresh()
    } catch (error) {
      toast.error(getErrorMessage(error, '保存失败'))
    }
  }

  const moreMenu = (record: Row): MenuProps['items'] => {
    const items: MenuProps['items'] = [
      {
        key: 'view',
        icon: <EyeOutlined />,
        label: '查看详情',
        onClick: () => setViewing(record),
      },
    ]
    if (canUpdate && record.is_custom) {
      items.push({
        key: 'reset',
        icon: <UndoOutlined />,
        label: '恢复默认（删除覆盖）',
        danger: true,
        onClick: () => confirmResetOne(record),
      })
    }
    return items
  }

  const renderPermissions = (record: Row) => {
    if (record.kind === 'group') return <span className="ms-muted">—</span>
    const perms = permList(record)
    if (perms.length === 0) return <span className="ms-muted">仅需登录</span>
    return (
      <span className="ms-perms">
        {perms.map((perm) => (
          <Tag key={perm} variant="filled">
            {perm}
          </Tag>
        ))}
      </span>
    )
  }

  const columns: TableProps<Row>['columns'] = [
    {
      title: '序号',
      key: 'index',
      width: 64,
      render: (_: unknown, record: Row) => (
        <span className="ms-index">{sequenceMap.get(record.key) ?? '—'}</span>
      ),
    },
    {
      title: '菜单名称',
      dataIndex: 'label',
      key: 'label',
      width: 184,
      ellipsis: true,
      render: (_: unknown, record: Row) => {
        const isGroup = record.kind === 'group'
        return (
          <span className={`ms-name${isGroup ? ' ms-name--group' : ''}`}>
            <span className="ms-name__icon">
              {menuIcon(record.icon) ?? (isGroup ? <MenuOutlined /> : null)}
            </span>
            <span className="ms-name__label">
              <span className="ms-name__text">{record.label}</span>
              {!record.module_enabled ? (
                <Tooltip title="模块未启用，侧栏不会显示该菜单">
                  <ExclamationCircleOutlined className="ms-warn" aria-label="模块关闭" />
                </Tooltip>
              ) : null}
            </span>
          </span>
        )
      },
    },
    {
      title: '类型',
      key: 'kind',
      width: 64,
      render: (_: unknown, record: Row) => (
        <span className="ms-type">
          <Tag variant="filled">{TYPE_LABELS[record.kind]}</Tag>
        </span>
      ),
    },
    {
      title: '菜单标题',
      dataIndex: 'label_en',
      key: 'title',
      width: 100,
      ellipsis: true,
      render: (value: string) => (
        <Tooltip title={`${value}（英文标题）`}>
          <span className="ms-title">{value}</span>
        </Tooltip>
      ),
    },
    {
      title: '权限标识',
      key: 'perm',
      width: 128,
      render: (_: unknown, record: Row) => renderPermissions(record),
    },
    {
      title: '状态',
      key: 'visible',
      width: 80,
      render: (_: unknown, record: Row) => {
        const isFooterGroup = record.kind === 'group' && record.key === FOOTER_GROUP_KEY
        return (
          <span className="ms-status">
            <Switch
              size="small"
              checked={record.visible}
              checkedChildren="启用"
              unCheckedChildren="停用"
              disabled={!canUpdate || isFooterGroup}
              onChange={(checked) => void updateVisible(record, checked)}
              aria-label={`${record.label} 是否可见`}
            />
          </span>
        )
      },
    },
    {
      title: '排序',
      key: 'order',
      width: 84,
      render: (_: unknown, record: Row) => {
        if (record.kind === 'group' && record.key === FOOTER_GROUP_KEY) {
          return <span className="ms-muted">固定</span>
        }
        const siblings = siblingsOf(record)
        const index = siblings.findIndex((item) => item.key === record.key)
        return (
          <Space size={2} className="ms-order">
            <span className="ms-order__num">{index + 1}</span>
            <Button
              type="text"
              size="small"
              aria-label={`${record.label} 上移`}
              disabled={!canUpdate || index <= 0}
              icon={<ArrowUpOutlined />}
              onClick={() => void move(record, -1)}
            />
            <Button
              type="text"
              size="small"
              aria-label={`${record.label} 下移`}
              disabled={!canUpdate || index < 0 || index >= siblings.length - 1}
              icon={<ArrowDownOutlined />}
              onClick={() => void move(record, 1)}
            />
          </Space>
        )
      },
    },
    {
      title: '路由名称',
      key: 'routeName',
      width: 100,
      ellipsis: true,
      render: (_: unknown, record: Row) => {
        if (record.key === FOOTER_GROUP_KEY) return <span className="ms-muted">—</span>
        return (
          <Tooltip title={record.key}>
            <code className="ms-code">{record.key}</code>
          </Tooltip>
        )
      },
    },
    {
      title: '路由路径',
      key: 'route',
      width: 116,
      ellipsis: true,
      render: (_: unknown, record: Row) => {
        const path = record.url ?? record.route
        if (!path) return <span className="ms-muted">—</span>
        return (
          <Tooltip title={path}>
            <code className="ms-code">{path}</code>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 128,
      fixed: 'right',
      render: (_: unknown, record: Row) => {
        if (record.kind === 'group' && record.key === FOOTER_GROUP_KEY) {
          return <span className="ms-muted">固定在页脚</span>
        }
        return (
          <span className="ms-ops">
            <IconAction
              label={`在 ${record.label} 下新增子菜单`}
              tip="菜单、路由与权限码由代码同源定义，请在后端 admin_menu.py 注册；此处不提供新增，避免 404/403 死入口"
              icon={<PlusOutlined />}
              disabled
            />
            <IconAction
              label={`查看 ${record.label}`}
              tip="查看详情"
              icon={<EyeOutlined />}
              onClick={() => setViewing(record)}
            />
            <IconAction
              label={`编辑 ${record.label}`}
              tip={canUpdate ? '编辑名称 / 英文标题 / 图标' : '当前账号没有 setting:update 权限'}
              icon={<EditOutlined />}
              disabled={!canUpdate}
              onClick={() => setEditing(record)}
            />
            <Dropdown
              trigger={['click']}
              placement="bottomRight"
              menu={{ items: moreMenu(record) }}
            >
              <Button
                type="text"
                size="small"
                aria-label={`更多操作 ${record.label}`}
                icon={<MoreOutlined />}
              />
            </Dropdown>
          </span>
        )
      },
    },
  ]

  const pagedRows = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return filteredRows.slice(start, start + PAGE_SIZE)
  }, [filteredRows, page])

  return (
    <div className="adm-page">
      <PageHeader
        icon={<MenuOutlined />}
        title="菜单管理"
        subtitle="调整侧边栏名称、英文标题、图标、顺序与可见性；菜单/路由/权限码仍由代码同源定义，不提供新建与删除"
        actions={
          <>
            <Button
              icon={allExpanded ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
              onClick={toggleAll}
            >
              {allExpanded ? '收起全部' : '展开全部'}
            </Button>
            <PermGuard perm="setting:update">
              <Button danger disabled={!data?.custom_count} onClick={confirmResetAll}>
                全部恢复默认
              </Button>
            </PermGuard>
          </>
        }
      />

      <ListToolbar
        hasFilter={hasFilter}
        onReset={resetFilters}
        extra={
          <Tooltip title="重新加载">
            <Button icon={<ReloadOutlined />} onClick={() => void load()} aria-label="重新加载菜单">
              刷新
            </Button>
          </Tooltip>
        }
      >
        <span className="ms-filter">
          <span className="ms-filter__label">菜单名称</span>
          <Input
            id="menu-settings-search"
            name="menu-settings-search"
            allowClear
            prefix={<SearchOutlined />}
            placeholder="请输入菜单名称"
            aria-label="菜单名称"
            style={{ width: 200 }}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setPage(1)
            }}
          />
        </span>
        <span className="ms-filter">
          <span className="ms-filter__label">类型</span>
          <Select
            aria-label="菜单类型"
            value={typeFilter}
            onChange={(value) => {
              setTypeFilter(value as TypeFilter)
              setPage(1)
            }}
            options={TYPE_FILTER_OPTIONS}
            style={{ width: 140 }}
          />
        </span>
        <span className="ms-filter">
          <span className="ms-filter__label">状态</span>
          <Select
            aria-label="菜单状态"
            value={statusFilter}
            onChange={(value) => {
              setStatusFilter(value as StatusFilter)
              setPage(1)
            }}
            options={STATUS_FILTER_OPTIONS}
            style={{ width: 140 }}
          />
        </span>
      </ListToolbar>

      <DataTable<Row>
        columns={columns}
        dataSource={pagedRows}
        rowKey="key"
        loading={loading}
        scrollX={1048}
        rowClassName={(record) => (record.kind === 'group' ? 'ms-group-row' : '')}
        expandable={{
          indentSize: 20,
          expandIconColumnIndex: 1,
          expandIcon: ({ expanded, onExpand, record }) => {
            const row = record as Row
            if ((row.children?.length ?? 0) === 0) {
              return <span className="ms-expand ms-expand--leaf" aria-hidden="true" />
            }
            return (
              <button
                type="button"
                className="ms-expand"
                aria-label={expanded ? `收起 ${row.label}` : `展开 ${row.label}`}
                aria-expanded={expanded}
                onClick={(event) => {
                  event.stopPropagation()
                  onExpand(row, event)
                }}
              >
                {expanded ? <DownOutlined /> : <RightOutlined />}
              </button>
            )
          },
        }}
        expandedRowKeys={expandedKeys}
        onExpandedRowsChange={(keys) => setExpandedKeys([...keys])}
        pagination={
          filteredRows.length > PAGE_SIZE
            ? {
                page,
                pageSize: PAGE_SIZE,
                total: filteredRows.length,
                onPageChange: setPage,
              }
            : undefined
        }
        empty={{
          hasFilter,
          filteredText: '没有匹配的菜单',
          emptyText: '暂无菜单',
          onReset: resetFilters,
        }}
      />

      <ModalForm
        open={editing !== null}
        title={editing ? `编辑「${editing.label}」` : '编辑菜单'}
        okText="保存"
        onCancel={() => setEditing(null)}
        onSubmit={(values) => submitEdit(values as MenuFormValues)}
        initialValues={
          editing
            ? { label_zh: editing.label_zh, label_en: editing.label_en, icon: editing.icon }
            : undefined
        }
      >
        <Form.Item
          label="中文名称"
          name="label_zh"
          extra={`留空使用代码默认值「${editing?.default_label_zh ?? ''}」`}
        >
          <Input maxLength={64} placeholder={editing?.default_label_zh} />
        </Form.Item>
        <Form.Item
          label="English name"
          name="label_en"
          extra={`留空使用默认值「${editing?.default_label_en ?? ''}」`}
        >
          <Input maxLength={64} placeholder={editing?.default_label_en} />
        </Form.Item>
        <Form.Item label="图标" name="icon" extra="留空使用代码默认图标">
          <Select
            allowClear
            showSearch
            placeholder={editing?.default_icon}
            options={MENU_ICON_NAMES.map((name) => ({
              value: name,
              label: (
                <Space size={6}>
                  {menuIcon(name)}
                  <span>{name}</span>
                </Space>
              ),
            }))}
          />
        </Form.Item>
      </ModalForm>

      <Drawer
        title={viewing ? `菜单详情 - ${viewing.label}` : '菜单详情'}
        size={420}
        open={viewing !== null}
        onClose={() => setViewing(null)}
        destroyOnHidden
      >
        {viewing && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="菜单名称">{viewing.label}</Descriptions.Item>
            <Descriptions.Item label="English name">{viewing.label_en}</Descriptions.Item>
            <Descriptions.Item label="类型">{TYPE_LABELS[viewing.kind]}</Descriptions.Item>
            <Descriptions.Item label="路由名称">
              {viewing.key === FOOTER_GROUP_KEY ? '—' : viewing.key}
            </Descriptions.Item>
            <Descriptions.Item label="路由路径">
              {viewing.url ?? viewing.route ?? '—'}
            </Descriptions.Item>
            <Descriptions.Item label="权限标识">
              {permList(viewing).join('、') || '仅需登录'}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              {viewing.visible ? '启用（显示中）' : '停用（已隐藏）'}
            </Descriptions.Item>
            <Descriptions.Item label="代码默认中文名">{viewing.default_label_zh}</Descriptions.Item>
            <Descriptions.Item label="代码默认英文名">{viewing.default_label_en}</Descriptions.Item>
            <Descriptions.Item label="代码默认图标">{viewing.default_icon}</Descriptions.Item>
            <Descriptions.Item label="显示设置">
              {viewing.is_custom ? '已覆盖（可恢复默认）' : '使用代码默认值'}
            </Descriptions.Item>
            <Descriptions.Item label="模块状态">
              {viewing.module_enabled ? '启用' : '已关闭'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  )
}
