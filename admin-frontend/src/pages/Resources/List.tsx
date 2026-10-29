import { useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, Input, Modal, Segmented, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { AppstoreOutlined, CodeOutlined, DatabaseOutlined, DeleteOutlined, DeploymentUnitOutlined, DownloadOutlined, EditOutlined, EyeOutlined, FileTextOutlined, HddOutlined, PlusOutlined, PushpinFilled, ReloadOutlined, StarFilled, ToolOutlined, UndoOutlined } from '@ant-design/icons'
import { apiPost } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import ContentThumb from '../../components/ContentThumb'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage } from '../../utils/error'
import { formatDate } from '../../utils/datetime'
import { shouldRetreatOnePage } from '../../utils/pagination'
import { RESOURCE_TYPES } from '../../constants'
import type { Resource } from '../../types'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
/** 推荐状态筛选项（value 直接映射 is_featured 布尔值） */
const FEATURED_FILTER_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'featured', label: '精选' },
  { value: 'normal', label: '普通' },
]

/** 回收站视图（正常内容 / 回收站）—— 视图切换，不是筛选条件 */
const RECYCLE_VIEW_OPTIONS = [
  { value: 'active', label: '正常内容' },
  { value: 'deleted', label: '回收站' },
]

/** 资源类型展示元数据：渐变底用于无封面时的图标占位块，配色用于类型胶囊 */
interface TypeMeta {
  gradient: string
  color: string
  bg: string
  icon: ReactNode
}

const TYPE_META: Record<string, TypeMeta> = {
  tool: {
    gradient: 'linear-gradient(135deg, #1677ff, #4096ff)',
    color: '#0958d9',
    bg: '#e6f4ff',
    icon: <ToolOutlined />,
  },
  model: {
    gradient: 'linear-gradient(135deg, #722ed1, #9254de)',
    color: '#531dab',
    bg: '#f9f0ff',
    icon: <DeploymentUnitOutlined />,
  },
  dataset: {
    gradient: 'linear-gradient(135deg, #13c2c2, #36cfc9)',
    color: '#006d75',
    bg: '#e6fffb',
    icon: <DatabaseOutlined />,
  },
  source: {
    gradient: 'linear-gradient(135deg, #52c41a, #73d13d)',
    color: '#389e0d',
    bg: '#f6ffed',
    icon: <CodeOutlined />,
  },
  doc: {
    gradient: 'linear-gradient(135deg, #fa8c16, #ffa940)',
    color: '#d46b08',
    bg: '#fff7e6',
    icon: <FileTextOutlined />,
  },
  other: {
    gradient: 'linear-gradient(135deg, #8c8c8c, #bfbfbf)',
    color: '#595959',
    bg: '#fafafa',
    icon: <AppstoreOutlined />,
  },
}

/** 未登记类型（分类可在「分类管理」自由新增）的兜底配色：中性灰，不报错也不突兀 */
const FALLBACK_TYPE_META: TypeMeta = {
  gradient: 'linear-gradient(135deg, #8c8c8c, #bfbfbf)',
  color: '#595959',
  bg: '#fafafa',
  icon: <AppstoreOutlined />,
}

const typeMeta = (type: string): TypeMeta => TYPE_META[type] || FALLBACK_TYPE_META

/** 类型展示名：命中内置类型用中文名，自定义分类原样展示 */
const typeLabel = (type: string): string => RESOURCE_TYPES.find((t) => t.value === type)?.label || type

/** 千分位格式化；缺值时返回「—」而不是 0（旧后端不返回该字段时不该谎报 0） */
function formatCount(value: number | undefined | null): string {
  if (typeof value !== 'number') return '—'
  return value.toLocaleString('zh-CN')
}

export default function ResourcesList() {
  const navigate = useNavigate()

  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  // 下拉筛选：空字符串表示不筛选
  const [resourceType, setResourceType] = useState('')
  const [featuredFilter, setFeaturedFilter] = useState('')
  // 回收站视图：'deleted' 时列表只显示已软删除的资源
  const [recycleView, setRecycleView] = useState('active')

  const params: Record<string, string | boolean> = {}
  if (keyword.trim()) params.keyword = keyword.trim()
  if (resourceType) params.resource_type = resourceType
  if (featuredFilter !== '') params.is_featured = featuredFilter === 'featured'
  if (recycleView === 'deleted') params.deleted = 'deleted'

  const { items, loading, page, pageSize, total, handleDelete, setPage, refresh } = useCrudList<Resource>({
    basePath: '/admin/resources',
    params: Object.keys(params).length ? params : undefined,
    deleteTitle: '移入回收站',
    deleteSuccessMessage: '已移入回收站（可在回收站恢复）',
    // 软删除必须说清「可恢复」，否则运营会以为点下去就没了
    deleteConfirm: (label) =>
      label ? `确认将 ${label} 移入回收站？可随时恢复，网盘链接会保留。` : '确认移入回收站？',
  })

  // 批量选择与操作
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [batchLoading, setBatchLoading] = useState(false)

  const handleSearch = (value: string) => {
    setKeyword(value)
    // 换了筛选条件就把选择清掉：否则「选中 A 页的几条 → 改筛选 → 批量删除」会删掉看不见的行
    setSelectedIds([])
    setPage(1)
  }

  const handleFilterChange = () => {
    setSelectedIds([])
    setPage(1)
  }

  /** 清空全部筛选条件并回到第一页 */
  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setResourceType('')
    setFeaturedFilter('')
    setRecycleView('active')
    setSelectedIds([])
    setPage(1)
  }

  const inRecycle = recycleView === 'deleted'

  const hasFilter = !!keyword.trim() || !!resourceType || featuredFilter !== '' || inRecycle

  const handleBatchDelete = () => {
    Modal.confirm({
      title: '批量移入回收站',
      content: `确认将选中的 ${selectedIds.length} 个资源移入回收站？网盘链接会保留，可随时恢复。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ deleted: number }>('/admin/resources/batch-delete', {
            ids: selectedIds,
          })
          toast.success(`已将 ${data.deleted} 个资源移入回收站`)
          setSelectedIds([])
          // 当前页删空时回退一页，避免停留在越界空页
          if (shouldRetreatOnePage(total - data.deleted, page, pageSize)) {
            setPage(page - 1)
          } else {
            refresh()
          }
        } catch (err) {
          toast.error(getErrorMessage(err, '批量删除失败'))
        } finally {
          setBatchLoading(false)
        }
      },
    })
  }

  const handleRestore = (r: Resource, republish = false) => {
    Modal.confirm({
      title: republish ? '恢复并发布' : '从回收站恢复',
      content: republish
        ? `确认恢复《${r.name}》并立即重新发布？该资源会马上对访客可见。`
        : `确认恢复《${r.name}》？恢复后是草稿状态，需要再点一次「发布」才会上线。`,
      okText: republish ? '恢复并发布' : '恢复为草稿',
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/admin/resources/${r.id}/restore?republish=${republish ? 'true' : 'false'}`)
          toast.success(republish ? '已恢复并发布' : '已恢复为草稿')
          setSelectedIds([])
          refresh()
        } catch (err) {
          toast.error(getErrorMessage(err, '恢复失败'))
        }
      },
    })
  }

  const handleBatchRestore = () => {
    Modal.confirm({
      title: '批量恢复',
      content: `确认恢复选中的 ${selectedIds.length} 个资源？恢复后为草稿状态，需再发布才上线。`,
      okText: '恢复为草稿',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ restored: number }>('/admin/resources/batch-restore', {
            ids: selectedIds,
          })
          toast.success(`已恢复 ${data.restored} 个资源（草稿）`)
          setSelectedIds([])
          refresh()
        } catch (err) {
          toast.error(getErrorMessage(err, '批量恢复失败'))
        } finally {
          setBatchLoading(false)
        }
      },
    })
  }

  const columns: TableProps<Resource>['columns'] = [
    {
      title: '资源',
      dataIndex: 'name',
      key: 'name',
      width: 320,
      render: (_: unknown, r: Resource) => {
        const tags = r.tags || []
        const meta = typeMeta(r.resource_type)
        return (
          <div className="adm-cell">
            <ContentThumb cover={r.cover_image} gradient={meta.gradient} icon={meta.icon} />
            <div className="adm-cell__body">
              <div className="adm-cell__name" title={r.name}>
                {r.name}
              </div>
              {r.description && <div className="adm-cell__desc">{r.description}</div>}
              {tags.length > 0 && (
                <div className="adm-cell__tags">
                  {tags.slice(0, 3).map((t) => (
                    <span className="adm-minitag" key={t}>
                      {t}
                    </span>
                  ))}
                  {tags.length > 3 && <span className="adm-minitag">+{tags.length - 3}</span>}
                </div>
              )}
            </div>
          </div>
        )
      },
    },
    {
      title: '类型',
      dataIndex: 'resource_type',
      key: 'resource_type',
      width: 112,
      render: (v: string) => {
        const meta = typeMeta(v)
        return (
          <span className="adm-typepill" style={{ color: meta.color, background: meta.bg }}>
            {meta.icon}
            {typeLabel(v)}
          </span>
        )
      },
    },
    {
      title: '大小',
      dataIndex: 'file_size',
      key: 'file_size',
      width: 88,
      render: (v: string) => <span className="adm-muted">{v || '—'}</span>,
    },
    {
      title: '下载',
      dataIndex: 'download_count',
      key: 'download_count',
      width: 92,
      align: 'right',
      render: (v: number) => (
        <span className="adm-metric">
          <DownloadOutlined />
          {formatCount(v)}
        </span>
      ),
    },
    {
      title: '预览',
      dataIndex: 'view_count',
      key: 'view_count',
      width: 92,
      align: 'right',
      render: (v: number | undefined) => (
        <span className="adm-metric">
          <EyeOutlined />
          {formatCount(v)}
        </span>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 172,
      render: (_: unknown, r: Resource) => (
        <Space size={4} wrap>
          {r.is_pinned && (
            <Tag color="red" icon={<PushpinFilled />} style={{ marginInlineEnd: 0 }}>
              置顶
            </Tag>
          )}
          {r.is_featured ? (
            <Tag color="gold" icon={<StarFilled />} style={{ marginInlineEnd: 0 }}>
              精选
            </Tag>
          ) : (
            !r.is_pinned && (
              <Tag style={{ marginInlineEnd: 0 }} color="default">
                普通
              </Tag>
            )
          )}
        </Space>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 112,
      render: (v: string | null) => <span className="adm-muted">{formatDate(v)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      fixed: 'right',
      render: (_: unknown, r: Resource) =>
        inRecycle ? (
          // 回收站视图只给恢复动作（不提供「彻底删除」：物理删除不可逆，没有保留期兜底）
          <Space size={0}>
            <PermGuard perm="resource:delete">
              <Button type="link" size="small" icon={<UndoOutlined />} onClick={() => handleRestore(r)}>
                恢复
              </Button>
            </PermGuard>
            <PermGuard perm="resource:delete">
              <Button type="link" size="small" onClick={() => handleRestore(r, true)}>
                恢复并发布
              </Button>
            </PermGuard>
          </Space>
        ) : (
          <Space size={0}>
            <PermGuard perm="resource:edit">
              <Button
                type="link"
                size="small"
                icon={<EditOutlined />}
                onClick={() => navigate(`/resources/${r.id}/edit`)}
              >
                编辑
              </Button>
            </PermGuard>
            <PermGuard perm="resource:delete">
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleDelete(r.id, `《${r.name}》`)}
              >
                删除
              </Button>
            </PermGuard>
          </Space>
        ),
    },
  ]

  return (
    <div className="adm-page">
      {/* 页头：图标 + 标题 + 说明 + 操作区 */}
      <div className="adm-header">
        <div className="adm-header__main">
          <span className="adm-header__icon">
            <HddOutlined />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="adm-header__title">资源管理</div>
            <div className="adm-header__sub">
              <span>维护站内资源、网盘链接与推荐状态</span>
              <span className="adm-header__dot">·</span>
              <span>
                共 <strong>{total}</strong> 条
              </span>
            </div>
          </div>
        </div>
        <div className="adm-header__actions">
          <Input.Search
            placeholder="搜索名称 / 简介"
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            onSearch={handleSearch}
            style={{ width: 240 }}
            allowClear
          />
          <PermGuard perm="resource:create">
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/resources/new')}>
              新建资源
            </Button>
          </PermGuard>
        </div>
      </div>

      {/* 工具条：筛选 + 批量操作 */}
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Segmented
              value={recycleView}
              onChange={(v) => {
                setRecycleView(String(v))
                handleFilterChange()
              }}
              options={RECYCLE_VIEW_OPTIONS}
            />
            <Select
              value={resourceType}
              onChange={(v) => {
                setResourceType(v)
                handleFilterChange()
              }}
              options={[
                { value: '', label: '全部类型' },
                ...RESOURCE_TYPES.map((t) => ({ value: t.value, label: t.label })),
              ]}
              style={{ width: 150 }}
            />
            <Segmented
              value={featuredFilter}
              onChange={(v) => {
                setFeaturedFilter(String(v))
                handleFilterChange()
              }}
              options={FEATURED_FILTER_OPTIONS}
            />
            {hasFilter && (
              <Tooltip title="清空关键词与筛选条件">
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={handleResetFilters}>
                  重置
                </Button>
              </Tooltip>
            )}
          </Space>
          <Space size={12}>
            {selectedIds.length > 0 && (
              <span className="adm-selinfo">
                已选 <strong>{selectedIds.length}</strong> 项
                <Button type="link" size="small" onClick={() => setSelectedIds([])}>
                  取消选择
                </Button>
              </span>
            )}
            {inRecycle ? (
              <PermGuard perm="resource:delete">
                <Button
                  icon={<UndoOutlined />}
                  disabled={!selectedIds.length}
                  loading={batchLoading}
                  onClick={handleBatchRestore}
                >
                  批量恢复
                </Button>
              </PermGuard>
            ) : (
              <PermGuard perm="resource:delete">
                <Button
                  danger
                  icon={<DeleteOutlined />}
                  disabled={!selectedIds.length}
                  loading={batchLoading}
                  onClick={handleBatchDelete}
                >
                  批量删除
                </Button>
              </PermGuard>
            )}
          </Space>
        </div>
      </Card>

      {/* 列表 */}
      <Card className="adm-tablecard" variant="outlined">
        <Table<Resource>
          className="adm-table"
          dataSource={items}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1128 }}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys as number[]),
          }}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的资源'
                emptyText={inRecycle ? '回收站是空的（删除的资源会出现在这里）' : '还没有资源，先创建第一个吧'}
                onReset={handleResetFilters}
                create={{ perm: 'resource:create', icon: <PlusOutlined />, label: '新建资源', onClick: () => navigate('/resources/new') }}
              />
          ),
          }}
          pagination={{
            current: page,
            pageSize,
            total,
            onChange: (p) => {
              setSelectedIds([])
              setPage(p)
            },
            showTotal: (t) => `共 ${t} 条`,
            showSizeChanger: false,
          }}
        />
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：删除为软删除，资源会进入「回收站」并可随时恢复（网盘链接保留）；恢复后默认是草稿状态。
      </Typography.Text>
    </div>
  )
}
