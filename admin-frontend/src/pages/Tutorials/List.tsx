import { useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, Input, Modal, Segmented, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { BookOutlined, CloudDownloadOutlined, CloudUploadOutlined, DeleteOutlined, EditOutlined, EyeOutlined, PushpinFilled, ReloadOutlined, StarFilled, UndoOutlined } from '@ant-design/icons'
import client, { apiPost } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import ContentThumb from '../../components/ContentThumb'
import { PermGuard } from '../../components/PermGuard'
import { useCategoryOptions } from '../../hooks/useCategoryOptions'
import { categoryMeta } from '../../utils/categoryColor'
import { getErrorMessage } from '../../utils/error'
import { formatDate } from '../../utils/datetime'
import { shouldRetreatOnePage } from '../../utils/pagination'
import { TUTORIAL_CATEGORIES, TUTORIAL_STATUSES } from '../../constants'
import type { Tutorial } from '../../types'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
/** 发布状态筛选项（value 直接映射 is_published 布尔值） */
const PUBLISH_FILTER_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'published', label: '已发布' },
  { value: 'draft', label: '草稿' },
]

/** 回收站视图（正常内容 / 回收站）—— 视图切换，不是筛选条件 */
const RECYCLE_VIEW_OPTIONS = [
  { value: 'active', label: '正常内容' },
  { value: 'deleted', label: '回收站' },
]

/** 阅读权限筛选项 */
const ACCESS_MODE_FILTER_OPTIONS = [
  { value: '', label: '全部权限' },
  { value: 'free', label: '免费' },
  { value: 'vip', label: '会员专享' },
]

/** 千分位；缺值显示「—」而不是 0（旧后端不返回该字段时不该谎报 0） */
function formatCount(value: number | undefined | null): string {
  if (typeof value !== 'number') return '—'
  return value.toLocaleString('zh-CN')
}

/** 更新状态标签 */
function statusTag(status: string): ReactNode {
  return status === 'completed' ? (
    <Tag color="success">已完结</Tag>
  ) : (
    <Tag color="warning">更新中</Tag>
  )
}

/** 阅读权限标签 */
function accessTag(t: Tutorial): ReactNode {
  if (t.access_mode === 'vip') {
    return <Tag color="gold">会员{t.points_price > 0 ? ` · ${t.points_price}积分` : ''}</Tag>
  }
  return <Tag color="blue">免费</Tag>
}

export default function TutorialsList() {
  const navigate = useNavigate()
  const categories = useCategoryOptions('tutorial', TUTORIAL_CATEGORIES)

  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  // 下拉筛选：空字符串表示不筛选
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState('')
  const [publishFilter, setPublishFilter] = useState('')
  const [accessMode, setAccessMode] = useState('')
  // 回收站视图：'deleted' 时列表只显示已软删除的教程（后端按 deleted 参数过滤）
  const [recycleView, setRecycleView] = useState('active')

  const params: Record<string, string | boolean> = {}
  if (keyword.trim()) params.keyword = keyword.trim()
  if (category) params.category = category
  if (status) params.status = status
  if (publishFilter !== '') params.is_published = publishFilter === 'published'
  if (accessMode) params.access_mode = accessMode
  if (recycleView === 'deleted') params.deleted = 'deleted'

  const { items: tutorials, loading, page, pageSize, total, refresh, handleDelete, setPage } = useCrudList<Tutorial>({
    basePath: '/admin/tutorials',
    params: Object.keys(params).length ? params : undefined,
    deleteTitle: '移入回收站',
    deleteSuccessMessage: '已移入回收站（可在回收站恢复）',
    // 软删除必须说清「可恢复」，否则运营不敢点，或者以为已经彻底删了
    deleteConfirm: (label) =>
      label ? `确认将 ${label} 移入回收站？可随时恢复，章节与购买记录会保留。` : '确认移入回收站？',
  })

  // 批量选择与操作
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [batchLoading, setBatchLoading] = useState(false)
  // 单条发布切换中（避免连点，同时给 Switch 之外的操作按钮一个 loading 反馈）
  const [publishingIds, setPublishingIds] = useState<number[]>([])

  const handleTogglePublish = async (t: Tutorial) => {
    setPublishingIds((prev) => [...prev, t.id])
    try {
      await client.patch(`/admin/tutorials/${t.id}/publish`)
      toast.success(t.is_published ? '已取消发布' : '已发布')
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setPublishingIds((prev) => prev.filter((x) => x !== t.id))
    }
  }

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
    setCategory('')
    setStatus('')
    setPublishFilter('')
    setAccessMode('')
    setRecycleView('active')
    setSelectedIds([])
    setPage(1)
  }

  const inRecycle = recycleView === 'deleted'

  const hasFilter =
    !!keyword.trim() || !!category || !!status || publishFilter !== '' || !!accessMode || inRecycle

  const handleBatchPublish = async (publish: boolean) => {
    setBatchLoading(true)
    try {
      const data = await apiPost<{ updated: number }>('/admin/tutorials/batch-publish', {
        ids: selectedIds,
        publish,
      })
      toast.success(`已${publish ? '发布' : '取消发布'} ${data.updated} 篇教程`)
      setSelectedIds([])
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, '批量操作失败'))
    } finally {
      setBatchLoading(false)
    }
  }

  const handleBatchDelete = () => {
    Modal.confirm({
      title: '批量移入回收站',
      content: `确认将选中的 ${selectedIds.length} 篇教程移入回收站？章节与购买记录会保留，可随时恢复。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ deleted: number }>('/admin/tutorials/batch-delete', {
            ids: selectedIds,
          })
          toast.success(`已将 ${data.deleted} 篇教程移入回收站`)
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

  const handleRestore = (t: Tutorial, republish = false) => {
    Modal.confirm({
      title: republish ? '恢复并发布' : '从回收站恢复',
      content: republish
        ? `确认恢复《${t.title}》并立即重新发布？该内容会马上对访客可见。`
        : `确认恢复《${t.title}》？恢复后是**草稿**状态，需要再点一次「发布」才会上线。`,
      okText: republish ? '恢复并发布' : '恢复为草稿',
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/admin/tutorials/${t.id}/restore?republish=${republish ? 'true' : 'false'}`)
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
      content: `确认恢复选中的 ${selectedIds.length} 篇教程？恢复后为草稿状态，需再发布才上线。`,
      okText: '恢复为草稿',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ restored: number }>('/admin/tutorials/batch-restore', {
            ids: selectedIds,
          })
          toast.success(`已恢复 ${data.restored} 篇教程（草稿）`)
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

  const columns: TableProps<Tutorial>['columns'] = [
    {
      title: '教程',
      dataIndex: 'title',
      key: 'title',
      width: 340,
      render: (_: unknown, t: Tutorial) => {
        const meta = categoryMeta(t.category)
        return (
          <div className="adm-cell">
            <ContentThumb cover={t.cover_image} gradient={meta.gradient} icon={<BookOutlined />} />
            <div className="adm-cell__body">
              <div className="adm-cell__titleline">
                <span className="adm-cell__name" title={t.title}>
                  {t.title}
                </span>
                {t.is_pinned && (
                  <Tag color="red" icon={<PushpinFilled />}>
                    置顶
                  </Tag>
                )}
                {t.is_recommended && (
                  <Tag color="orange" icon={<StarFilled />}>
                    推荐
                  </Tag>
                )}
              </div>
              {t.description && <div className="adm-cell__desc">{t.description}</div>}
            </div>
          </div>
        )
      },
    },
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 104,
      render: (v: string) => {
        const meta = categoryMeta(v)
        return (
          <span className="adm-typepill" style={{ color: meta.text, background: meta.bg }}>
            {v}
          </span>
        )
      },
    },
    {
      title: '更新状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => statusTag(v),
    },
    {
      title: '发布状态',
      dataIndex: 'is_published',
      key: 'is_published',
      width: 100,
      render: (v: boolean) =>
        v ? <Tag color="success">已发布</Tag> : <Tag color="default">草稿</Tag>,
    },
    {
      title: '阅读权限',
      dataIndex: 'access_mode',
      key: 'access_mode',
      width: 124,
      render: (_: unknown, t: Tutorial) => accessTag(t),
    },
    {
      title: '学习',
      dataIndex: 'view_count',
      key: 'view_count',
      width: 88,
      align: 'right',
      render: (v: number | undefined) => (
        <span className="adm-metric">
          <EyeOutlined />
          {formatCount(v)}
        </span>
      ),
    },
    {
      title: '最近更新',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 108,
      render: (v: string | null | undefined) => <span className="adm-muted">{formatDate(v)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      fixed: 'right',
      render: (_: unknown, t: Tutorial) =>
        inRecycle ? (
          // 回收站视图：只给恢复动作。刻意不提供「彻底删除」——
          // 物理删除不可逆，当前没有「保留期到期自动清理」的兜底，误点就真没了
          <Space size={0}>
            <PermGuard perm="tutorial:delete">
              <Button type="link" size="small" icon={<UndoOutlined />} onClick={() => handleRestore(t)}>
                恢复
              </Button>
            </PermGuard>
            <PermGuard perm="tutorial:delete">
              <Button type="link" size="small" onClick={() => handleRestore(t, true)}>
                恢复并发布
              </Button>
            </PermGuard>
          </Space>
        ) : (
          <Space size={0}>
            <PermGuard perm="tutorial:edit">
              <Button
                type="link"
                size="small"
                loading={publishingIds.includes(t.id)}
                onClick={() => handleTogglePublish(t)}
              >
                {t.is_published ? '取消发布' : '发布'}
              </Button>
            </PermGuard>
            <PermGuard perm="tutorial:edit">
              <Button
                type="link"
                size="small"
                icon={<EditOutlined />}
                onClick={() => navigate(`/tutorials/${t.id}/edit`)}
              >
                编辑
              </Button>
            </PermGuard>
            <PermGuard perm="tutorial:delete">
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleDelete(t.id, `《${t.title}》`)}
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
      {/* 页头 */}
      <div className="adm-header">
        <div className="adm-header__main">
          <span className="adm-header__icon">
            <BookOutlined />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="adm-header__title">教程管理</div>
            <div className="adm-header__sub">
              <span>维护教程基本信息、章节内容与阅读权限</span>
              <span className="adm-header__dot">·</span>
              <span>
                共 <strong>{total}</strong> 条
              </span>
            </div>
          </div>
        </div>
        <div className="adm-header__actions">
          <Input.Search
            placeholder="搜索标题 / 简介"
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            onSearch={handleSearch}
            style={{ width: 240 }}
            allowClear
          />
          <PermGuard perm="tutorial:create">
            <Button type="primary" icon={<BookOutlined />} onClick={() => navigate('/tutorials/new')}>
              新建教程
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
              value={category}
              onChange={(v) => {
                setCategory(v)
                handleFilterChange()
              }}
              options={[{ value: '', label: '全部分类' }, ...categories.map((c) => ({ value: c, label: c }))]}
              style={{ width: 140 }}
            />
            <Select
              value={status}
              onChange={(v) => {
                setStatus(v)
                handleFilterChange()
              }}
              options={[{ value: '', label: '全部更新状态' }, ...TUTORIAL_STATUSES]}
              style={{ width: 140 }}
            />
            <Segmented
              value={publishFilter}
              onChange={(v) => {
                setPublishFilter(String(v))
                handleFilterChange()
              }}
              options={PUBLISH_FILTER_OPTIONS}
            />
            <Select
              value={accessMode}
              onChange={(v) => {
                setAccessMode(v)
                handleFilterChange()
              }}
              options={ACCESS_MODE_FILTER_OPTIONS}
              style={{ width: 130 }}
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
              <PermGuard perm="tutorial:delete">
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
              <>
                <PermGuard perm="tutorial:edit">
                  <Button
                    icon={<CloudUploadOutlined />}
                    disabled={!selectedIds.length}
                    loading={batchLoading}
                    onClick={() => handleBatchPublish(true)}
                  >
                    批量发布
                  </Button>
                </PermGuard>
                <PermGuard perm="tutorial:edit">
                  <Button
                    icon={<CloudDownloadOutlined />}
                    disabled={!selectedIds.length}
                    loading={batchLoading}
                    onClick={() => handleBatchPublish(false)}
                  >
                    批量取消发布
                  </Button>
                </PermGuard>
                <PermGuard perm="tutorial:delete">
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
              </>
            )}
          </Space>
        </div>
      </Card>

      {/* 列表 */}
      <Card className="adm-tablecard" variant="outlined">
        <Table<Tutorial>
          className="adm-table"
          dataSource={tutorials}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1164 }}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys as number[]),
          }}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的教程'
                emptyText={inRecycle ? '回收站是空的（删除的教程会出现在这里）' : '还没有教程，先创建第一篇吧'}
                onReset={handleResetFilters}
                create={{ perm: 'tutorial:create', icon: <BookOutlined />, label: '新建教程', onClick: () => navigate('/tutorials/new') }}
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
        提示：批量删除会一并清除所选教程的章节与购买记录，操作不可恢复。
      </Typography.Text>
    </div>
  )
}
