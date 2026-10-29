import { useState } from 'react'
import { Button, Card, ColorPicker, DatePicker, Form, Input, Modal, Segmented, Select, Space, Switch, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import type { Dayjs } from 'dayjs'
import dayjs from 'dayjs'
import { CloudDownloadOutlined, CloudUploadOutlined, DeleteOutlined, EditOutlined, ReloadOutlined, SoundOutlined } from '@ant-design/icons'
import { apiPost, apiPut } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { shouldRetreatOnePage } from '../../utils/pagination'
import { formatTime } from '../../utils/datetime'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
import PageHeader from '../../components/ui/PageHeader'
interface Announcement {
  id: number
  title: string
  content: string
  tag: string
  color: string
  is_published: boolean
  published_at: string | null
  /** 定时发布时间（NULL=立即；未来时间=到点才可见） */
  publish_at?: string | null
  created_at: string | null
  updated_at: string | null
}

/** 标签预设（与前台公告条展示一致，DB 列宽 20） */
const TAG_OPTIONS = [
  { value: '公告', label: '公告' },
  { value: '活动', label: '活动' },
  { value: '资讯', label: '资讯' },
  { value: '更新', label: '更新' },
]

const DEFAULT_COLOR = '#3b82f6'

/** 发布状态筛选项（value 直接映射 is_published 布尔值） */
const PUBLISH_FILTER_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'published', label: '已发布' },
  { value: 'draft', label: '草稿' },
]

/** 列表摘要：正文是纯文本（可能含少量 HTML），去掉标签与多余空白后再交给 CSS 截断 */
function excerpt(content: string): string {
  return (content || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
}

/** 标签胶囊：底色用标签色的 10% 透明度（与前台公告条的配色算法一致） */
function tagPill(tag: string, color: string) {
  const c = color || DEFAULT_COLOR
  return (
    <span className="adm-typepill" style={{ color: c, background: `${c}1a` }}>
      {tag}
    </span>
  )
}

export default function AnnouncementsList() {
  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [publishFilter, setPublishFilter] = useState('')

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Announcement | null>(null)
  const [togglingId, setTogglingId] = useState<number | null>(null)
  const [form] = Form.useForm()

  const listParams: Record<string, string | boolean> = {}
  if (keyword.trim()) listParams.keyword = keyword.trim()
  if (tagFilter) listParams.tag = tagFilter
  if (publishFilter !== '') listParams.is_published = publishFilter === 'published'

  const { items, loading, page, pageSize, total, refresh, handleDelete, setPage } =
    useCrudList<Announcement>({
      basePath: '/admin/announcements',
      params: Object.keys(listParams).length ? listParams : undefined,
      deleteTitle: '确认删除公告',
      deleteSuccessMessage: '删除成功',
    })

  // 批量选择与操作
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [batchLoading, setBatchLoading] = useState(false)

  const hasFilter = !!keyword.trim() || !!tagFilter || publishFilter !== ''

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ tag: '公告', color: DEFAULT_COLOR, is_published: true })
    setModalOpen(true)
  }

  const openEdit = (record: Announcement) => {
    setEditing(record)
    form.setFieldsValue({
      title: record.title,
      content: record.content,
      tag: record.tag,
      color: record.color,
      is_published: record.is_published,
      publish_at: record.publish_at ? dayjs(record.publish_at) : null,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      const payload = {
        title: values.title.trim(),
        content: values.content,
        tag: values.tag,
        color: typeof values.color === 'string' ? values.color : values.color.toHexString(),
        is_published: values.is_published,
        // 定时发布：DatePicker 给的是本地时区的 dayjs，统一转成 ISO（UTC）再交给后端，
        // 避免「本地 20:00 被当成 UTC 20:00」整体偏 8 小时
        publish_at: values.publish_at ? (values.publish_at as Dayjs).toISOString() : null,
      }
      if (editing === null) {
        await apiPost('/admin/announcements', payload)
        toast.success('公告创建成功')
      } else {
        await apiPut(`/admin/announcements/${editing.id}`, payload)
        toast.success('公告已更新')
      }
      setModalOpen(false)
      refresh()
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '操作失败'))
    }
  }

  /** 发布 / 撤下快捷切换 */
  const handleTogglePublish = async (record: Announcement) => {
    setTogglingId(record.id)
    try {
      await apiPut(`/admin/announcements/${record.id}`, { is_published: !record.is_published })
      toast.success(record.is_published ? '已撤下' : '已发布')
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setTogglingId(null)
    }
  }

  /** 换筛选/搜索就把已选清掉：否则「选中 A 页的几条 → 改筛选 → 批量删除」会删掉看不见的行 */
  const handleSearch = (value: string) => {
    setKeyword(value)
    setSelectedIds([])
    setPage(1)
  }

  const handleFilterChange = () => {
    setSelectedIds([])
    setPage(1)
  }

  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setTagFilter('')
    setPublishFilter('')
    setSelectedIds([])
    setPage(1)
  }

  const handleBatchPublish = async (publish: boolean) => {
    setBatchLoading(true)
    try {
      const data = await apiPost<{ updated: number }>('/admin/announcements/batch-publish', {
        ids: selectedIds,
        publish,
      })
      toast.success(`已${publish ? '发布' : '撤下'} ${data.updated} 条公告`)
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
      title: '批量删除',
      content: `确认删除选中的 ${selectedIds.length} 条公告？不可恢复。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ deleted: number }>('/admin/announcements/batch-delete', {
            ids: selectedIds,
          })
          toast.success(`已删除 ${data.deleted} 条公告`)
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

  const columns: TableProps<Announcement>['columns'] = [
    {
      title: '公告',
      dataIndex: 'title',
      key: 'title',
      width: 420,
      render: (_: unknown, record: Announcement) => (
        <div className="ann-cell">
          {/* 左侧色条直接用公告自身的标签色，扫一眼就能和前台公告条对上 */}
          <span className="ann-cell__bar" style={{ background: record.color || DEFAULT_COLOR }} />
          <div className="ann-cell__body">
            <div className="adm-cell__name" title={record.title}>
              {record.title}
            </div>
            {record.content && <div className="adm-cell__desc">{excerpt(record.content)}</div>}
          </div>
        </div>
      ),
    },
    {
      title: '标签',
      dataIndex: 'tag',
      key: 'tag',
      width: 110,
      render: (v: string, record: Announcement) => tagPill(v, record.color),
    },
    {
      title: '状态',
      dataIndex: 'is_published',
      key: 'is_published',
      width: 100,
      render: (v: boolean) =>
        v ? <Tag color="success">已发布</Tag> : <Tag color="default">草稿</Tag>,
    },
    {
      title: '发布时间',
      dataIndex: 'published_at',
      key: 'published_at',
      width: 150,
      render: (v: string | null) => <span className="adm-muted">{formatTime(v)}</span>,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 150,
      render: (v: string | null) => <span className="adm-muted">{formatTime(v)}</span>,
    },
    {
      title: '定时发布',
      dataIndex: 'publish_at',
      key: 'publish_at',
      width: 150,
      render: (value: string | null) => {
        if (!value) return <Typography.Text type="secondary">—</Typography.Text>
        const isFuture = new Date(value).getTime() > Date.now()
        return (
          <Tooltip title={isFuture ? '未到点：前台还看不到，到时间自动出现' : '已到点，正常展示'}>
            <Tag color={isFuture ? 'processing' : 'default'}>{formatTime(value)}</Tag>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      fixed: 'right',
      render: (_: unknown, record: Announcement) => {
        const publishBtn = (
          <Button
            type="link"
            size="small"
            loading={togglingId === record.id}
            onClick={() => handleTogglePublish(record)}
          >
            {record.is_published ? '撤下' : '发布'}
          </Button>
        )
        return (
          <Space size={0}>
            <PermGuard perm="announcement:edit">
              {record.is_published || !record.published_at ? (
                publishBtn
              ) : (
                // 撤下过的公告再发布会沿用原发布时间 → 前台仍按旧时间排序，这里明确告知
                <Tooltip
                  title={`重新发布不会刷新发布时间（仍为 ${formatTime(record.published_at)}），首页按该时间排序`}
                >
                  <span>{publishBtn}</span>
                </Tooltip>
              )}
            </PermGuard>
            <PermGuard perm="announcement:edit">
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
                编辑
              </Button>
            </PermGuard>
            {/* handleDelete（useCrudList）内部已弹 Modal.confirm，此处直接调用避免双重确认 */}
            <PermGuard perm="announcement:delete">
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleDelete(record.id, `公告「${record.title}」`)}
              >
                删除
              </Button>
            </PermGuard>
          </Space>
        )
      },
    },
  ]

  return (
    <div className="adm-page">
      {/* 页头：不放在 PermGuard 里 —— 原先整个页头（含搜索框）被 announcement:create 包着，
          只有「能新增公告」的人才看得见，与其余模块「页头对 announcement:view 可见」不一致 */}
      <PageHeader
        icon={<SoundOutlined />}
        title="公告管理"
        subtitle="发布后的公告展示在首页公告条，撤下即从首页移除"
        count={{ total: total }}
        actions={
          <>
            <Input.Search
              placeholder="搜索公告标题"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onSearch={handleSearch}
              style={{ width: 220 }}
              allowClear
            />
            <PermGuard perm="announcement:create">
              <Button type="primary" icon={<SoundOutlined />} onClick={openCreate}>
                新增公告
              </Button>
            </PermGuard>

          </>
        }
      />

      {/* 工具条：筛选 + 批量操作 */}
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Select
              value={tagFilter}
              onChange={(v) => {
                setTagFilter(v)
                handleFilterChange()
              }}
              options={[{ value: '', label: '全部标签' }, ...TAG_OPTIONS]}
              style={{ width: 130 }}
            />
            <Segmented
              value={publishFilter}
              onChange={(v) => {
                setPublishFilter(String(v))
                handleFilterChange()
              }}
              options={PUBLISH_FILTER_OPTIONS}
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
            <PermGuard perm="announcement:edit">
              <Button
                icon={<CloudUploadOutlined />}
                disabled={!selectedIds.length}
                loading={batchLoading}
                onClick={() => handleBatchPublish(true)}
              >
                批量发布
              </Button>
            </PermGuard>
            <PermGuard perm="announcement:edit">
              <Button
                icon={<CloudDownloadOutlined />}
                disabled={!selectedIds.length}
                loading={batchLoading}
                onClick={() => handleBatchPublish(false)}
              >
                批量撤下
              </Button>
            </PermGuard>
            <PermGuard perm="announcement:delete">
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
          </Space>
        </div>
      </Card>

      {/* 列表 */}
      <Card className="adm-tablecard" variant="outlined">
        <Table<Announcement>
          className="adm-table"
          dataSource={items}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1130 }}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys as number[]),
          }}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的公告'
                emptyText='还没有公告，先创建一条吧'
                onReset={handleResetFilters}
                create={{ perm: 'announcement:create', icon: <SoundOutlined />, label: '新增公告', onClick: openCreate }}
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
        提示：首页公告条按发布时间倒序轮播<strong>最新 5 条</strong>已发布公告；撤下后重新发布
        <strong>不会刷新发布时间</strong>，需要让一条旧公告回到最前时请新建一条。
      </Typography.Text>

      <Modal
        title={editing === null ? '新增公告' : '编辑公告'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        width={680}
        destroyOnHidden
        forceRender
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="title"
            label="标题"
            rules={[
              { required: true, message: '请输入公告标题' },
              { max: 200, message: '标题最长 200 字' },
            ]}
            extra="展示在首页公告条与「查看全部」弹窗里，建议 30 字以内"
          >
            <Input placeholder="请输入公告标题" showCount maxLength={200} />
          </Form.Item>

          <Form.Item
            name="content"
            label="正文"
            rules={[{ required: true, message: '请输入公告正文' }]}
            extra="纯文本。前台按 Markdown 渲染：需要分段请空一行（只按一次回车会被合并成一行）。"
          >
            <Input.TextArea
              placeholder={'请输入公告正文\n\n空一行即可分段'}
              autoSize={{ minRows: 6, maxRows: 14 }}
              maxLength={5000}
              showCount
            />
          </Form.Item>

          <Space size={24} align="start" style={{ display: 'flex' }}>
            <Form.Item
              name="tag"
              label="标签"
              style={{ minWidth: 140, marginBottom: 0 }}
              rules={[{ required: true, message: '请选择标签' }]}
            >
              <Select options={TAG_OPTIONS} placeholder="公告标签" />
            </Form.Item>
            <Form.Item
              name="color"
              label="标签颜色"
              initialValue={DEFAULT_COLOR}
              style={{ marginBottom: 0 }}
              rules={[{ required: true, message: '请选择标签颜色' }]}
            >
              <ColorPicker showText format="hex" disabledAlpha />
            </Form.Item>
            {/* 实时预览：前台公告条里这个标签长什么样（底色是标签色的 10% 透明度） */}
            <Form.Item label="效果预览" style={{ marginBottom: 0 }}>
              <Form.Item
                noStyle
                shouldUpdate={(prev, cur) => prev.tag !== cur.tag || prev.color !== cur.color}
              >
                {() => {
                  const tag = form.getFieldValue('tag') || '公告'
                  const raw = form.getFieldValue('color')
                  const color = typeof raw === 'string' ? raw : raw?.toHexString?.() || DEFAULT_COLOR
                  return <div style={{ paddingTop: 4 }}>{tagPill(tag, color)}</div>
                }}
              </Form.Item>
            </Form.Item>
          </Space>

          <Form.Item
            name="is_published"
            label="发布状态"
            valuePropName="checked"
            style={{ marginTop: 20, marginBottom: 0 }}
            extra="关闭时保存为草稿，不展示在前台"
          >
            <Switch checkedChildren="已发布" unCheckedChildren="草稿" />
          </Form.Item>

          {/* 定时发布：不填=立即（沿用发布状态）；填未来时间=到点才出现在前台 */}
          <Form.Item
            name="publish_at"
            label="定时发布"
            style={{ marginTop: 16, marginBottom: 0 }}
            extra="留空 = 立即按「发布状态」生效；填未来时间则到点才展示（发布状态仍需为「已发布」）"
          >
            <DatePicker
              showTime={{ format: 'HH:mm' }}
              format="YYYY-MM-DD HH:mm"
              placeholder="选择上线时间（可选）"
              style={{ width: 240 }}
              allowClear
            />
          </Form.Item>

          {editing !== null && editing.published_at && (
            <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
              这条公告曾发布过（原发布时间 {formatTime(editing.published_at)}）；再次发布会沿用该时间，
              首页仍按它排序。
            </Typography.Text>
          )}
        </Form>
      </Modal>
    </div>
  )
}
