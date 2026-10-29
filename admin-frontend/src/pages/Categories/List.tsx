import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Dropdown,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Tabs,
  Tooltip,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderOutlined,
  MoreOutlined,
  PlusOutlined,
  SearchOutlined,
  TagsOutlined,
} from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import { clearCategoryCache } from '../../utils/categories'
import { formatDate } from '../../utils/datetime'
import { getErrorMessage } from '../../utils/error'

import { toast } from '../../utils/toast'
import PageHeader from '../../components/ui/PageHeader'
import { DataTable, ListToolbar } from '../../components/ui'

/**
 * 可管理的分类模块 —— **从后端取**（`GET /categories` 派生自内容类型注册表）。
 *
 * 这里曾经写死过 `tutorial / resource / node / plugin / tool` 五个 Tab：模块下线后
 * 前端不会报错，只是留下「点进去新增必 422」的死入口。清单必须与后端同源。
 */
interface CategoryModule {
  key: string
  label: string
}

/** 与后端 NAME_MAX_LENGTH（categories.name 的 String(50)）对齐 */
const NAME_MAX_LENGTH = 50

interface CategoryRow {
  id: number
  name: string
  /** 所属分组；null = 未分组 */
  group_id: number | null
  sort_order: number
  /** 该分类下的内容条数（实时统计，不走分类缓存） */
  content_count: number
  created_at: string | null
}

interface CategoryGroupRow {
  id: number
  name: string
  sort_order: number
  category_count: number
}

/** 内容里出现、分类表未登记的取值（前台按它筛选会筛不到东西） */
interface OrphanCategory {
  name: string
  content_count: number
}

interface CategoryOverview {
  module: string
  items: CategoryRow[]
  groups: CategoryGroupRow[]
  /** 未分组分类数（虚拟组，不是真实记录） */
  ungrouped_count: number
  orphans: OrphanCategory[]
  total_content: number
}

/** 左侧的选中项：全部 / 未分组 / 某个真实分组 */
type Selection = 'all' | 'ungrouped' | number

const USAGE_FILTER_OPTIONS = [
  { value: 'all', label: '全部分类' },
  { value: 'unused', label: '仅未使用' },
]

export default function CategoriesList() {
  const [modules, setModules] = useState<CategoryModule[]>([])
  const [activeModule, setActiveModule] = useState('')
  const [overview, setOverview] = useState<CategoryOverview | null>(null)
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [usageFilter, setUsageFilter] = useState('all')
  const [reordering, setReordering] = useState(false)

  /** 左侧选中的分组 */
  const [selection, setSelection] = useState<Selection>('all')

  // 分类弹窗
  const [modalOpen, setModalOpen] = useState(false)
  /** 正在编辑的分类；null = 新增 */
  const [editingRow, setEditingRow] = useState<CategoryRow | null>(null)
  const [name, setName] = useState('')
  /** 分类归属的分组；null = 未分组 */
  const [groupId, setGroupId] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // 分组弹窗（新增 / 重命名共用）
  const [groupModalOpen, setGroupModalOpen] = useState(false)
  const [editingGroup, setEditingGroup] = useState<CategoryGroupRow | null>(null)
  const [groupName, setGroupName] = useState('')
  const [groupSubmitting, setGroupSubmitting] = useState(false)

  const load = useCallback(async (module: string) => {
    setLoading(true)
    try {
      // 走 /overview 而非公开的 /categories/{module}：内容数量必须实时 ——
      // 公开接口带 120s Redis 缓存，且缓存只由分类写操作失效，内容增删不触发，
      // 拿缓存数字当「0 条 → 可以删」的依据会误导运营
      const data = await apiGet<CategoryOverview>(`/categories/${module}/overview`)
      setOverview(data)
    } catch (err) {
      toast.error(getErrorMessage(err, '加载分类失败'))
      setOverview(null)
    } finally {
      setLoading(false)
    }
  }, [])

  // 模块清单只取一次：分类页的 Tab/分组结构由内容类型注册表决定，不由前端决定
  useEffect(() => {
    let alive = true
    apiGet<{ modules: CategoryModule[] }>('/categories')
      .then((data) => {
        if (!alive) return
        const list = data.modules ?? []
        setModules(list)
        setActiveModule((prev) => prev || list[0]?.key || '')
      })
      .catch(() => {
        if (!alive) return
        setModules([])
        setActiveModule('')
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!activeModule) return
    load(activeModule)
  }, [activeModule, load])

  // 用 useMemo 固定引用：`a ?? []` 每次渲染都会造新数组，会让下面几个 useMemo 全部失效
  const items = useMemo(() => overview?.items ?? [], [overview])
  const groups = useMemo(() => overview?.groups ?? [], [overview])
  const orphans = useMemo(() => overview?.orphans ?? [], [overview])

  const groupNameOf = useMemo(() => {
    const map = new Map<number, string>()
    groups.forEach((g) => map.set(g.id, g.name))
    return map
  }, [groups])

  /** 分类 id → 在全量顺序里的位置（1 起）。筛选后仍显示真实序号，避免看起来「顺序乱了」 */
  const orderOf = useMemo(() => {
    const map = new Map<number, number>()
    items.forEach((it, index) => map.set(it.id, index + 1))
    return map
  }, [items])

  /** 右侧列表：先按左侧选中的分组过滤，再叠搜索与「仅未使用」 */
  const rows = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    return items.filter((it) => {
      if (selection === 'ungrouped' && it.group_id !== null) return false
      if (typeof selection === 'number' && it.group_id !== selection) return false
      if (usageFilter === 'unused' && it.content_count > 0) return false
      if (kw && !it.name.toLowerCase().includes(kw)) return false
      return true
    })
  }, [items, selection, keyword, usageFilter])

  const hasFilter = !!keyword.trim() || usageFilter !== 'all'
  /** 排序调整作用于**全量**顺序：筛选/分组视图下「上移」的邻居不是真实邻居，禁用比给出错序更诚实 */
  const reorderLocked = hasFilter || selection !== 'all'

  const handleModuleChange = (key: string) => {
    setActiveModule(key)
    setSelection('all')
    // 换模块保留「仅未使用」这个查看偏好，但清掉关键词（新模块里多半搜不到）
    setKeyword('')
  }

  const handleResetFilters = () => {
    setKeyword('')
    setUsageFilter('all')
  }

  // ---------------- 分类 ----------------

  const openCreate = () => {
    setEditingRow(null)
    setName('')
    // 在某个分组下点「新增分类」时预填该分组，省一次选择
    setGroupId(typeof selection === 'number' ? selection : null)
    setModalOpen(true)
  }

  const openEdit = (row: CategoryRow) => {
    setEditingRow(row)
    setName(row.name)
    setGroupId(row.group_id)
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const trimmed = name.trim()
    if (!trimmed) {
      toast.warning('请输入分类名称')
      return
    }
    setSubmitting(true)
    try {
      if (editingRow === null) {
        await apiPost('/categories', { module: activeModule, name: trimmed, group_id: groupId })
        toast.success('新增成功')
      } else {
        // group_id 传 null 时后端视为「不改分组」，要移出分组请用左栏拖放/编辑里的选择
        await apiPut(`/categories/${editingRow.id}`, { name: trimmed, group_id: groupId })
        toast.success('更新成功')
      }
      // 清掉本地分类缓存，编辑页/筛选下拉下次拉取即为最新
      clearCategoryCache()
      setModalOpen(false)
      load(activeModule)
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (row: CategoryRow) => {
    try {
      await apiDelete(`/categories/${row.id}`)
      toast.success('删除成功')
      clearCategoryCache()
      load(activeModule)
    } catch (err) {
      toast.error(getErrorMessage(err, '删除失败'))
    }
  }

  /** 上移 / 下移：整组重排一次提交（分两次 PUT 交换，中途失败会留下并列排序） */
  const handleMove = async (row: CategoryRow, delta: -1 | 1) => {
    const index = items.findIndex((it) => it.id === row.id)
    const target = index + delta
    if (index < 0 || target < 0 || target >= items.length) return

    const next = [...items]
    ;[next[index], next[target]] = [next[target], next[index]]
    setReordering(true)
    try {
      await apiPut(`/categories/${activeModule}/reorder`, { ids: next.map((it) => it.id) })
      clearCategoryCache()
      // 服务端已按新顺序连续赋值，本地直接重排省一次往返
      setOverview((prev) =>
        prev ? { ...prev, items: next.map((it, i) => ({ ...it, sort_order: i + 1 })) } : prev,
      )
      toast.success('排序已更新')
    } catch (err) {
      toast.error(getErrorMessage(err, '排序失败'))
      load(activeModule) // 失败时以服务端为准，别让本地顺序和后台分叉
    } finally {
      setReordering(false)
    }
  }

  // ---------------- 分组 ----------------

  const openGroupCreate = () => {
    setEditingGroup(null)
    setGroupName('')
    setGroupModalOpen(true)
  }

  const openGroupRename = (group: CategoryGroupRow) => {
    setEditingGroup(group)
    setGroupName(group.name)
    setGroupModalOpen(true)
  }

  const handleGroupSubmit = async () => {
    const trimmed = groupName.trim()
    if (!trimmed) {
      toast.warning('请输入分组名称')
      return
    }
    setGroupSubmitting(true)
    try {
      if (editingGroup === null) {
        await apiPost('/admin/category-groups', { module: activeModule, name: trimmed })
        toast.success('分组已创建')
      } else {
        await apiPut(`/admin/category-groups/${editingGroup.id}`, { name: trimmed })
        toast.success('分组已重命名')
      }
      setGroupModalOpen(false)
      await load(activeModule)
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setGroupSubmitting(false)
    }
  }

  const handleGroupDelete = async (group: CategoryGroupRow) => {
    try {
      await apiDelete(`/admin/category-groups/${group.id}`)
      toast.success('分组已删除')
      if (selection === group.id) setSelection('all')
      await load(activeModule)
    } catch (err) {
      toast.error(getErrorMessage(err, '删除分组失败'))
    }
  }

  const columns: TableProps<CategoryRow>['columns'] = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 110,
      render: (value: number) => <span className="adm-num">{value}</span>,
    },
    {
      title: '序号',
      dataIndex: 'sort_order',
      key: 'sort_order',
      width: 76,
      // 左对齐：作为前置索引列，右对齐会与「分类」之间空出一大截，数字看起来是飘的
      align: 'left',
      render: (_: unknown, row: CategoryRow) => (
        <span className="adm-muted">{orderOf.get(row.id) ?? '—'}</span>
      ),
    },
    {
      title: '分类',
      dataIndex: 'name',
      key: 'name',
      // 显式给宽度：不给的话这一列会吃掉全部剩余空间，右边几列会被推得离内容很远
      // （table-layout: fixed 下无宽度列独占余量，有宽度的列则按比例分摊）
      width: 240,
      // 纯文本，不加颜色标签：分类名本身已经足够辨识，六种彩色底纹排下来反而花
      render: (v: string) => <span className="adm-cell__name">{v}</span>,
    },
    {
      title: '分组',
      dataIndex: 'group_id',
      key: 'group_id',
      width: 140,
      render: (v: number | null) =>
        v === null ? (
          <span className="adm-muted">未分组</span>
        ) : (
          <span>{groupNameOf.get(v) ?? `分组 #${v}`}</span>
        ),
    },
    {
      title: '内容数量',
      dataIndex: 'content_count',
      key: 'content_count',
      width: 110,
      align: 'right',
      render: (value: number) =>
        value > 0 ? (
          <span className="adm-metric">{value}</span>
        ) : (
          <span className="adm-muted">未使用</span>
        ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 120,
      render: (v: string | null) => <span className="adm-muted">{formatDate(v)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 232,
      fixed: 'right',
      render: (_: unknown, row: CategoryRow) => {
        const index = orderOf.get(row.id) ?? 0
        const lockTip = reorderLocked ? '请先切到「全部」并清空筛选，再调整排序' : ''
        return (
          <Space size={4}>
            <PermGuard perm="category:edit">
              <Tooltip title={lockTip || (index <= 1 ? '已在最前' : '上移一位')}>
                <span>
                  <Button
                    type="text"
                    size="small"
                    icon={<ArrowUpOutlined />}
                    disabled={reorderLocked || index <= 1}
                    loading={reordering}
                    onClick={() => handleMove(row, -1)}
                  />
                </span>
              </Tooltip>
              <Tooltip title={lockTip || (index >= items.length ? '已在最后' : '下移一位')}>
                <span>
                  <Button
                    type="text"
                    size="small"
                    icon={<ArrowDownOutlined />}
                    disabled={reorderLocked || index >= items.length}
                    loading={reordering}
                    onClick={() => handleMove(row, 1)}
                  />
                </span>
              </Tooltip>
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>
                编辑
              </Button>
            </PermGuard>
            <PermGuard perm="category:delete">
              {row.content_count > 0 ? (
                // 有内容时删除必被后端拒绝（提示「该分类下有 N 条内容,无法删除」）——
                // 直接置灰并说明原因，比让运营点一次再吃错误更省事
                <Tooltip title={`该分类下有 ${row.content_count} 条内容，需先把内容改到其它分类才能删除`}>
                  <span>
                    <Button type="link" size="small" danger disabled icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </span>
                </Tooltip>
              ) : (
                <Popconfirm
                  title={`确认删除分类「${row.name}」？`}
                  description="该分类下没有内容，删除后不可恢复。"
                  okText="删除"
                  okType="danger"
                  cancelText="取消"
                  onConfirm={() => handleDelete(row)}
                >
                  <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                    删除
                  </Button>
                </Popconfirm>
              )}
            </PermGuard>
          </Space>
        )
      },
    },
  ]

  const activeLabel = modules.find((m) => m.key === activeModule)?.label ?? ''
  const selectedGroupName =
    selection === 'all'
      ? '全部分类'
      : selection === 'ungrouped'
        ? '未分组'
        : (groupNameOf.get(selection) ?? '')

  return (
    <div className="adm-page">
      {/* 页头：不放在 PermGuard 里 —— 原先整个页头（含搜索框）被 category:create 包着，
          只有「能新增分类」的人才看得见，与其余模块「页头对 category:view 可见、只有按钮受守卫」不一致 */}
      <PageHeader
        icon={<TagsOutlined />}
        title="分类管理"
        subtitle={[
          '左侧管理分组，右侧管理该分组下的分类',
          <>共 <strong>{items.length}</strong> 个分类</>,
          <>{groups.length} 个分组</>,
          <>关联内容 <strong>{overview?.total_content ?? 0}</strong> 条</>,
        ]}
        actions={
          <>
            <Input
              placeholder="搜索分类名称"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              prefix={<SearchOutlined style={{ color: '#bfbfbf' }} />}
              style={{ width: 200 }}
              allowClear
            />
            <PermGuard perm="category:create">
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                新增分类
              </Button>
            </PermGuard>
          </>
        }
      />

      {modules.length > 1 ? (
        // 多种内容类型 = 多套分组，每个 Tab 是一种内容类型
        <Tabs
          activeKey={activeModule}
          onChange={handleModuleChange}
          items={modules.map((m) => ({ key: m.key, label: m.label }))}
        />
      ) : (
        <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
          内容类型：{activeLabel || '加载中…'}
        </Typography.Text>
      )}

      {/* 孤儿分类：内容在用、分类表没有的取值 —— 不报错，只是前台按它筛选永远筛不到 */}
      {orphans.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title={`${activeLabel}模块有 ${orphans.length} 个分类未在分类表中登记`}
          description={
            <>
              <div style={{ marginBottom: 4 }}>
                以下取值只出现在内容里（括号内为内容数）：
                {orphans.map((o) => `${o.name}（${o.content_count}）`).join('、')}
              </div>
              <div>
                它们没有对应的分类记录，前台按这些值筛选时可能筛不到内容 ——
                建议新增同名分类，或把内容改到已登记的分类上。
              </div>
            </>
          }
        />
      )}

      <Row gutter={16} align="top">
        {/* 左栏：分组 */}
        <Col flex="280px">
          <Card
            className="adm-sidecard"
            variant="outlined"
            title={
              <span>
                <FolderOutlined style={{ marginRight: 6, color: '#8c8c8c' }} />
                分组
              </span>
            }
            extra={
              <PermGuard perm="category:create">
                <Tooltip title="新建分组">
                  <Button type="text" size="small" icon={<PlusOutlined />} onClick={openGroupCreate} />
                </Tooltip>
              </PermGuard>
            }
          >
            <List
              size="small"
              dataSource={[
                { key: 'all' as Selection, name: '全部分类', count: items.length },
                {
                  key: 'ungrouped' as Selection,
                  name: '未分组',
                  count: overview?.ungrouped_count ?? 0,
                },
                ...groups.map((g) => ({
                  key: g.id as Selection,
                  name: g.name,
                  count: g.category_count,
                  group: g,
                })),
              ]}
              renderItem={(entry) => {
                const active = selection === entry.key
                const group = 'group' in entry ? (entry.group as CategoryGroupRow) : undefined
                return (
                  <List.Item
                    onClick={() => setSelection(entry.key)}
                    style={{
                      cursor: 'pointer',
                      padding: '8px 16px',
                      background: active ? 'rgba(66, 99, 235, 0.08)' : undefined,
                      borderInlineStart: active ? '3px solid #4263EB' : '3px solid transparent',
                    }}
                    extra={
                      group ? (
                        <Dropdown
                          trigger={['click']}
                          menu={{
                            items: [
                              { key: 'rename', label: '重命名' },
                              { key: 'delete', label: '删除', danger: true },
                            ],
                            onClick: ({ key, domEvent }) => {
                              domEvent.stopPropagation()
                              if (key === 'rename') openGroupRename(group)
                              if (key === 'delete') {
                                Modal.confirm({
                                  title: `确认删除分组「${group.name}」？`,
                                  content:
                                    group.category_count > 0
                                      ? `该分组下还有 ${group.category_count} 个分类，请先把它们移到别的分组（或未分组）再删除。`
                                      : '分组删除后不可恢复；分类本身不受影响。',
                                  okText: '删除',
                                  okType: 'danger',
                                  cancelText: '取消',
                                  onOk: () => handleGroupDelete(group),
                                })
                              }
                            },
                          }}
                        >
                          <Button
                            type="text"
                            size="small"
                            icon={<MoreOutlined />}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </Dropdown>
                      ) : null
                    }
                  >
                    <Space size={6} style={{ width: '100%', justifyContent: 'space-between' }}>
                      <span style={{ fontWeight: active ? 600 : 400 }}>{entry.name}</span>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {entry.count}
                      </Typography.Text>
                    </Space>
                  </List.Item>
                )
              }}
            />
          </Card>
        </Col>

        {/* 右栏：当前分组下的分类 */}
        <Col flex="auto" style={{ minWidth: 0 }}>
          <ListToolbar
            hasFilter={hasFilter}
            onReset={handleResetFilters}
            extra={(
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                当前：{selectedGroupName} · 列表顺序即前台展示顺序
              </Typography.Text>
            )}
          >
            <Segmented
              value={usageFilter}
              onChange={(v) => setUsageFilter(String(v))}
              options={USAGE_FILTER_OPTIONS}
            />
          </ListToolbar>

          <DataTable<CategoryRow>
            dataSource={rows}
            columns={columns}
            rowKey="id"
            loading={loading}
            scrollX={924}
            empty={{
              hasFilter: hasFilter || selection !== 'all',
              filteredText: '该分组下没有符合条件的分类',
              emptyText: '该分组下还没有分类',
              onReset: () => {
                handleResetFilters()
                setSelection('all')
              },
            }}
          />
        </Col>
      </Row>

      <Modal
        title={editingRow === null ? `新增${activeLabel}分类` : '编辑分类'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        confirmLoading={submitting}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Select
            value={groupId ?? 0}
            onChange={(v) => setGroupId(Number(v) === 0 ? null : Number(v))}
            style={{ width: '100%' }}
            options={[
              { value: 0, label: '未分组' },
              ...groups.map((g) => ({ value: g.id, label: g.name })),
            ]}
          />
          <Input
            placeholder="请输入分类名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onPressEnter={handleSubmit}
            maxLength={NAME_MAX_LENGTH}
            showCount
          />
          {editingRow !== null && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {editingRow.content_count > 0
                ? `该分类下有 ${editingRow.content_count} 条内容，改名会同步更新这些内容的分类字段。`
                : '该分类下暂无内容，改名不会影响任何内容。'}
            </Typography.Text>
          )}
        </Space>
      </Modal>

      <Modal
        title={editingGroup === null ? '新建分组' : '重命名分组'}
        open={groupModalOpen}
        onOk={handleGroupSubmit}
        onCancel={() => setGroupModalOpen(false)}
        confirmLoading={groupSubmitting}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
      >
        <Input
          placeholder="请输入分组名称（如：入门 / 进阶）"
          value={groupName}
          onChange={(e) => setGroupName(e.target.value)}
          onPressEnter={handleGroupSubmit}
          maxLength={NAME_MAX_LENGTH}
          showCount
        />
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
          分组只用于后台归类，前台筛选用的是「分类」；改名不影响任何内容。
        </Typography.Text>
      </Modal>
    </div>
  )
}
