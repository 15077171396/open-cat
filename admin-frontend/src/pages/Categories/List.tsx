import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Input, Modal, Popconfirm, Segmented, Space, Table, Tabs, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, TagsOutlined } from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import { clearCategoryCache } from '../../utils/categories'
import { formatDate } from '../../utils/datetime'
import { getErrorMessage } from '../../utils/error'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
import PageHeader from '../../components/ui/PageHeader'
/** 可管理的分类模块（与后端 MODULE_TABLE_MAP 一致；`tool` 曾因 schema 白名单漏配而无法新增） */
type ModuleKey = 'tutorial' | 'resource' | 'node' | 'plugin' | 'tool'

const MODULES: { key: ModuleKey; label: string }[] = [
  { key: 'tutorial', label: '教程' },
  { key: 'resource', label: '资源' },
  { key: 'node', label: '知识百科' },
  { key: 'plugin', label: '扩展' },
  { key: 'tool', label: '在线工具' },
]

/** 与后端 NAME_MAX_LENGTH（categories.name 的 String(50)）对齐 */
const NAME_MAX_LENGTH = 50

interface CategoryRow {
  id: number
  name: string
  sort_order: number
  /** 该分类下的内容条数（实时统计，不走分类缓存） */
  content_count: number
  created_at: string | null
}

/** 内容里出现、分类表未登记的取值（前台按它筛选会筛不到东西） */
interface OrphanCategory {
  name: string
  content_count: number
}

interface CategoryOverview {
  module: string
  items: CategoryRow[]
  orphans: OrphanCategory[]
  total_content: number
}

const USAGE_FILTER_OPTIONS = [
  { value: 'all', label: '全部分类' },
  { value: 'unused', label: '仅未使用' },
]

export default function CategoriesList() {
  const [activeModule, setActiveModule] = useState<ModuleKey>('tutorial')
  const [overview, setOverview] = useState<CategoryOverview | null>(null)
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [usageFilter, setUsageFilter] = useState('all')
  const [reordering, setReordering] = useState(false)

  const [modalOpen, setModalOpen] = useState(false)
  /** 正在编辑的分类；null = 新增 */
  const [editingRow, setEditingRow] = useState<CategoryRow | null>(null)
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async (module: ModuleKey) => {
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

  useEffect(() => {
    load(activeModule)
  }, [activeModule, load])

  // 用 useMemo 固定引用：`a ?? []` 每次渲染都会造新数组，会让下面几个 useMemo 全部失效
  const items = useMemo(() => overview?.items ?? [], [overview])
  const orphans = useMemo(() => overview?.orphans ?? [], [overview])

  /** 分类 id → 在全量顺序里的位置（1 起）。筛选后仍显示真实序号，避免看起来「顺序乱了」 */
  const orderOf = useMemo(() => {
    const map = new Map<number, number>()
    items.forEach((it, index) => map.set(it.id, index + 1))
    return map
  }, [items])

  const rows = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    return items.filter((it) => {
      if (usageFilter === 'unused' && it.content_count > 0) return false
      if (kw && !it.name.toLowerCase().includes(kw)) return false
      return true
    })
  }, [items, keyword, usageFilter])

  const unusedCount = items.filter((it) => it.content_count === 0).length
  const hasFilter = !!keyword.trim() || usageFilter !== 'all'
  /** 排序调整作用于**全量**顺序：筛选状态下「上移」的邻居不是真实邻居，禁用比给出错序更诚实 */
  const reorderLocked = hasFilter

  const handleModuleChange = (key: string) => {
    setActiveModule(key as ModuleKey)
    // 换模块保留「仅未使用」这个查看偏好，但清掉关键词（新模块里多半搜不到）
    setKeyword('')
  }

  const handleResetFilters = () => {
    setKeyword('')
    setUsageFilter('all')
  }

  const openCreate = () => {
    setEditingRow(null)
    setName('')
    setModalOpen(true)
  }

  const openEdit = (row: CategoryRow) => {
    setEditingRow(row)
    setName(row.name)
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
        await apiPost('/categories', { module: activeModule, name: trimmed })
        toast.success('新增成功')
      } else {
        await apiPut(`/categories/${editingRow.id}`, { name: trimmed })
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
      width: 80,
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
      width: 280,
      // 纯文本，不加颜色标签：分类名本身已经足够辨识，六种彩色底纹排下来反而花
      render: (v: string) => <span className="adm-cell__name">{v}</span>,
    },
    {
      title: '内容数量',
      dataIndex: 'content_count',
      key: 'content_count',
      width: 120,
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
        const lockTip = reorderLocked ? '请先清空搜索与筛选条件，再调整排序' : ''
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

  const activeLabel = MODULES.find((m) => m.key === activeModule)?.label ?? ''

  return (
    <div className="adm-page">
      {/* 页头：不放在 PermGuard 里 —— 原先整个页头（含搜索框）被 category:create 包着，
          只有「能新增分类」的人才看得见，与其余模块「页头对 category:view 可见、只有按钮受守卫」不一致 */}
      <PageHeader
        icon={<TagsOutlined />}
        title="分类管理"
        subtitle={[
            "维护各模块的内容分类，供前台筛选与编辑页下拉使用",
            <>共 <strong>{items.length}</strong> 个分类</>,
            <>未使用 <strong>{unusedCount}</strong> 个</>,
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

      <Tabs
        activeKey={activeModule}
        onChange={handleModuleChange}
        items={MODULES.map((m) => ({ key: m.key, label: m.label }))}
      />

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

      {/* 工具条 */}
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Segmented
              value={usageFilter}
              onChange={(v) => setUsageFilter(String(v))}
              options={USAGE_FILTER_OPTIONS}
            />
            {hasFilter && (
              <Tooltip title="清空搜索与筛选条件">
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={handleResetFilters}>
                  重置
                </Button>
              </Tooltip>
            )}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            列表顺序即前台展示顺序，用操作列的 ↑ ↓ 调整
          </Typography.Text>
        </div>
      </Card>

      {/* 列表 */}
      <Card className="adm-tablecard" variant="outlined">
        <Table<CategoryRow>
          className="adm-table"
          dataSource={rows}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 942 }}
          pagination={false}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的分类'
                emptyText={`${activeLabel}模块还没有分类，先新增一个吧`}
                onReset={handleResetFilters}
                create={{ perm: 'category:create', icon: <PlusOutlined />, label: '新增分类', onClick: openCreate }}
              />
          ),
          }}
        />
      </Card>

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
        <Input
          placeholder="请输入分类名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onPressEnter={handleSubmit}
          maxLength={NAME_MAX_LENGTH}
          showCount
        />
        {editingRow !== null && (
          <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
            {editingRow.content_count > 0
              ? `该分类下有 ${editingRow.content_count} 条内容，改名会同步更新这些内容的分类字段。`
              : '该分类下暂无内容，改名不会影响任何内容。'}
          </Typography.Text>
        )}
      </Modal>
    </div>
  )
}
