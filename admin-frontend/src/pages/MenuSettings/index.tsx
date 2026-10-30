import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Key } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
} from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  EditOutlined,
  MenuOutlined,
  ReloadOutlined,
  UndoOutlined,
} from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { PageHeader, DataTable, ModalForm } from '../../components/ui'
import { MENU_ICON_NAMES, menuIcon } from '../../constants/menuIcons'
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

const FOOTER_GROUP_KEY = '__footer__'

function permList(entry: MenuEntry): string[] {
  return entry.perm ? [entry.perm, ...entry.perms] : entry.perms
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

/**
 * 菜单管理（展示层）。
 *
 * 我们的菜单 / 路由 / 权限码是**代码同源**的（路由表守卫会在 CI 里比对三者），
 * 所以这里刻意不提供新建/删除：凭空加一条菜单只会得到点进去 404 或 403 的死入口。
 * 页面负责调整名称、图标、顺序、可见性；数据存 DB 覆盖，清空即回到代码默认。
 */
export default function MenuSettings() {
  const [data, setData] = useState<MenuSettingsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])
  const [editing, setEditing] = useState<Row | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await apiGet<MenuSettingsData>('/admin/menu-settings')
      setData(result)
      setExpandedKeys([...result.groups.map((group) => group.key), FOOTER_GROUP_KEY])
    } catch {
      toast.error('菜单加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const rows = useMemo<Row[]>(() => {
    if (!data) return []
    const groups: Row[] = data.groups.map((group) => ({
      ...group,
      children: (group.items ?? []).map((item) => ({ ...item, parentKey: group.key })),
    }))
    if (data.footer.length > 0) {
      groups.push({
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
    return groups
  }, [data])

  const filteredRows = useMemo<Row[]>(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return rows
    const result: Row[] = []
    for (const group of rows) {
      const groupMatch = matches(group, keyword)
      const children = (group.children ?? []).filter((child) => matches(child, keyword))
      if (groupMatch || children.length > 0) {
        result.push({ ...group, children: groupMatch ? group.children : children })
      }
    }
    return result
  }, [rows, query])

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
    } catch {
      toast.error('排序保存失败')
    }
  }

  const updateVisible = async (record: Row, visible: boolean) => {
    try {
      await apiPut('/admin/menu-settings/item', { key: record.key, visible })
      if (record.key === '/menu-settings' && !visible) {
        toast.warning('已隐藏本页入口；需要直接访问 /admin/menu-settings 才能回来')
      }
      await refresh()
    } catch {
      toast.error('可见性保存失败')
    }
  }

  const resetOne = async (record: Row) => {
    try {
      await apiDelete('/admin/menu-settings/item', { params: { key: record.key } })
      toast.success('已恢复代码默认值')
      await refresh()
    } catch {
      toast.error('恢复失败')
    }
  }

  const resetAll = async () => {
    try {
      const result = await apiPost<{ reset: number }>('/admin/menu-settings/reset')
      toast.success(result.reset > 0 ? `已恢复 ${result.reset} 项默认值` : '没有需要恢复的项')
      await refresh()
    } catch {
      toast.error('恢复失败')
    }
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
    } catch {
      toast.error('保存失败')
    }
  }

  const columns = [
    {
      title: '菜单',
      dataIndex: 'label',
      key: 'label',
      width: 300,
      render: (_: unknown, record: Row) => {
        const isGroup = record.kind === 'group'
        const perms = permList(record)
        return (
          <span className={`ms-name${isGroup ? ' ms-name--group' : ''}`}>
            <span className="ms-name__icon">{menuIcon(record.icon) ?? (isGroup ? <MenuOutlined /> : null)}</span>
            <span className="ms-name__text">
              <span className="ms-name__label">
                {record.label}
                {record.is_custom ? <Tag color="blue">已修改</Tag> : null}
                {!record.module_enabled ? <Tag>模块关闭</Tag> : null}
              </span>
              <span className="ms-name__sub">
                {record.kind === 'group' ? '分组' : `English: ${record.label_en}`}
                {perms.length > 0 ? ` · ${perms.join(' / ')}` : ''}
              </span>
            </span>
          </span>
        )
      },
    },
    {
      title: '路由 / 权限',
      key: 'route',
      width: 260,
      responsive: ['md' as const],
      render: (_: unknown, record: Row) => {
        if (record.kind === 'group') return <span className="ms-muted">分组节点</span>
        const perms = permList(record)
        return (
          <div className="ms-route">
            <code>{record.route || record.url || '—'}</code>
            {perms.length > 0 ? (
              <span className="ms-perms">
                {perms.map((perm) => (
                  <Tag key={perm} bordered={false}>
                    {perm}
                  </Tag>
                ))}
              </span>
            ) : (
              <span className="ms-muted">仅需登录</span>
            )}
          </div>
        )
      },
    },
    {
      title: '可见',
      key: 'visible',
      width: 86,
      render: (_: unknown, record: Row) => (
        <Switch
          size="small"
          checked={record.visible}
          disabled={record.kind === 'group' && record.key === FOOTER_GROUP_KEY}
          onChange={(checked) => void updateVisible(record, checked)}
          aria-label={`${record.label} 是否可见`}
        />
      ),
    },
    {
      title: '排序',
      key: 'order',
      width: 92,
      responsive: ['lg' as const],
      render: (_: unknown, record: Row) => {
        if (record.kind === 'group' && record.key === FOOTER_GROUP_KEY) return null
        const siblings = siblingsOf(record)
        const index = siblings.findIndex((item) => item.key === record.key)
        return (
          <Space size={0} className="ms-order">
            <Button
              type="text"
              size="small"
              aria-label="上移"
              disabled={index <= 0}
              icon={<ArrowUpOutlined />}
              onClick={() => void move(record, -1)}
            />
            <Button
              type="text"
              size="small"
              aria-label="下移"
              disabled={index < 0 || index >= siblings.length - 1}
              icon={<ArrowDownOutlined />}
              onClick={() => void move(record, 1)}
            />
          </Space>
        )
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 170,
      render: (_: unknown, record: Row) => {
        if (record.kind === 'group' && record.key === FOOTER_GROUP_KEY) {
          return <span className="ms-muted">固定在页脚</span>
        }
        return (
          <Space size={4}>
            <Button size="small" type="link" icon={<EditOutlined />} onClick={() => setEditing(record)}>
              编辑
            </Button>
            {record.is_custom && (
              <Popconfirm
                title="恢复默认显示？"
                description="将清除这项目名称 / 图标 / 可见性 / 排序覆盖。"
                onConfirm={() => void resetOne(record)}
              >
                <Button size="small" type="link" icon={<UndoOutlined />}>
                  恢复默认
                </Button>
              </Popconfirm>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <div>
      <PageHeader
        icon={<MenuOutlined />}
        title="菜单管理"
        subtitle="调整侧边栏的显示名称、图标、顺序与可见性；路由与权限码仍由代码同源定义"
        actions={
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜索名称 / 路由 / 权限码"
              style={{ width: 240 }}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Tooltip title="重新加载">
              <Button icon={<ReloadOutlined />} onClick={() => void load()} aria-label="重新加载菜单" />
            </Tooltip>
            <Popconfirm
              title="恢复全部菜单默认显示？"
              description="所有自定义名称、图标、排序与可见性都会被清除。"
              onConfirm={() => void resetAll()}
            >
              <Button danger disabled={!data?.custom_count}>
                全部恢复默认
              </Button>
            </Popconfirm>
          </Space>
        }
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        title="菜单不会凭空新增或删除"
        description="菜单、路由、权限码由代码与 RBAC 同源定义。这里只调整展示层，避免出现侧栏有入口、点进去 404（无路由）或 403（权限码未登记）的死菜单。代码里的默认值可通过「恢复默认」找回。"
      />

      <DataTable<Row>
        columns={columns}
        dataSource={filteredRows}
        rowKey="key"
        loading={loading}
        scrollX={940}
        rowClassName={(record) => (record.kind === 'group' ? 'ms-group-row' : '')}
        expandedRowKeys={expandedKeys}
        onExpandedRowsChange={(keys) => setExpandedKeys([...keys])}
        empty={{
          hasFilter: query.trim().length > 0,
          filteredText: '没有匹配的菜单',
          emptyText: '暂无菜单',
          onReset: () => setQuery(''),
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
    </div>
  )
}
