import { useCallback, useEffect, useState } from 'react'
import { Button, DatePicker, Form, Grid, Image, Input, Modal, Segmented, Select, Space, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import type { Dayjs } from 'dayjs'
import { CommentOutlined, DeleteOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiGet, apiPut } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { formatTime } from '../../utils/datetime'
import type { FeedbackStatus } from '../../types'

import { toast } from '../../utils/toast'
import PageHeader from '../../components/ui/PageHeader'
import { DataTable, ListToolbar } from '../../components/ui'
/**
 * 用户反馈：用户在前台右侧工具栏提交，后台审阅（改状态 / 写内部备注 / 删除）。
 *
 * ⚠️ 状态中文名取接口返回的 `status_label`，筛选下拉取 `status_options` ——
 * 本文件**不保留**「代码 → 中文名」的映射表。理由见 `app/models/feedback.py`：
 * 两端各手写一份的话，后端加状态时前端不会报错，只会静默显示成裸代码。
 * 这里只保留**颜色**映射（纯展示，未知值回落 default）。
 */

/** 状态 → 标签配色（纯展示；未知值回落 default，不影响正确性） */
const STATUS_COLOR: Record<string, string> = {
  pending: 'orange',
  processing: 'blue',
  resolved: 'green',
  closed: 'default',
}

interface Option {
  value: string
  label: string
}

/** /admin/feedbacks 列表项 */
interface FeedbackItem {
  id: number
  user_id: number
  user_account?: string | null
  user_nickname?: string | null
  content: string
  contact?: string | null
  /** 截图 URL（本站 COS），可能为空数组；纯截图反馈的 content 只有类型前缀 */
  images?: string[] | null
  status: FeedbackStatus
  status_label?: string
  admin_note?: string | null
  created_at: string | null
  updated_at: string | null
}

export default function FeedbackList() {
  const screens = Grid.useBreakpoint()
  // md 以下：内容优先，次要列隐藏；操作列不再 fixed，避免浮在正文上
  const isMobile = screens.md === false
  const [statusFilter, setStatusFilter] = useState('')
  const [statusOptions, setStatusOptions] = useState<Option[]>([])
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  /** 时间范围：存 dayjs 以便回填控件，提交时才转 ISO（后端统一换算成 naive UTC） */
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null)

  const [detail, setDetail] = useState<FeedbackItem | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const listParams: Record<string, string | number | boolean> = {}
  if (statusFilter) listParams.status = statusFilter
  if (keyword.trim()) listParams.keyword = keyword.trim()
  if (range) {
    listParams.start_at = range[0].startOf('day').toISOString()
    listParams.end_at = range[1].endOf('day').toISOString()
  }

  const { items, loading, page, pageSize, total, refresh, handleDelete, setPage } =
    useCrudList<FeedbackItem>({
      basePath: '/admin/feedbacks',
      params: Object.keys(listParams).length ? listParams : undefined,
      deleteTitle: '确认删除反馈',
      deleteSuccessMessage: '反馈已删除',
    })

  const hasFilter = !!statusFilter || !!keyword.trim() || !!range

  // 筛选项与中文名都来自接口：后端加状态时这里自动跟上
  const loadStatusOptions = useCallback(async () => {
    try {
      const data = await apiGet<{ status_options?: Option[] }>('/admin/feedbacks', {
        params: { page: 1, page_size: 1 },
      })
      setStatusOptions(data.status_options || [])
    } catch {
      // 候选取不到不影响列表本身，静默降级
    }
  }, [])

  useEffect(() => {
    loadStatusOptions()
  }, [loadStatusOptions])

  const handleResetFilters = () => {
    setStatusFilter('')
    setKeywordInput('')
    setKeyword('')
    setRange(null)
    setPage(1)
  }

  /** 打开审阅弹框：回填当前状态与备注 */
  const openDetail = (record: FeedbackItem) => {
    setDetail(record)
    form.setFieldsValue({ status: record.status, admin_note: record.admin_note || '' })
  }

  /** 保存审阅：状态 + 管理员备注 */
  const handleSave = async () => {
    if (!detail) return
    try {
      const values = await form.validateFields()
      setSaving(true)
      await apiPut(`/admin/feedbacks/${detail.id}`, {
        status: values.status,
        admin_note: values.admin_note?.trim() || null,
      })
      toast.success('反馈已更新')
      setDetail(null)
      refresh()
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setSaving(false)
    }
  }

  /** 快捷标记为已解决 */
  const handleQuickResolve = async (record: FeedbackItem) => {
    try {
      await apiPut(`/admin/feedbacks/${record.id}`, { status: 'resolved' })
      toast.success('已标记为已解决')
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    }
  }

  const columns: TableProps<FeedbackItem>['columns'] = [
    {
      title: '提交人',
      key: 'user',
      width: 150,
      responsive: ['md'],
      render: (_: unknown, row: FeedbackItem) => (
        <span className="adm-cell__name">
          {row.user_nickname || row.user_account || `用户 ${row.user_id}`}
        </span>
      ),
    },
    {
      title: '提交人账号',
      dataIndex: 'user_account',
      key: 'user_account',
      width: 150,
      responsive: ['md'],
      render: (v: string | null | undefined) =>
        v ? (
          <Typography.Text ellipsis={{ tooltip: v }} style={{ maxWidth: 140 }}>
            {v}
          </Typography.Text>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '反馈内容',
      dataIndex: 'content',
      key: 'content',
      width: isMobile ? 260 : 280,
      render: (v: string, row: FeedbackItem) => (
        <div>
          <Typography.Paragraph
            ellipsis={{ tooltip: v, rows: 2 }}
            style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
          >
            {v || <span className="adm-muted">（仅截图）</span>}
          </Typography.Paragraph>
          {row.images?.length ? (
            // PreviewGroup：点任意缩略图可在遮罩里左右翻看全部截图
            <Image.PreviewGroup>
              <Space size={6} style={{ marginTop: 6 }}>
                {row.images.map((url) => (
                  <Image
                    key={url}
                    src={url}
                    width={44}
                    height={44}
                    style={{ objectFit: 'cover', borderRadius: "var(--app-radius)" }}
                    alt="反馈截图"
                  />
                ))}
              </Space>
            </Image.PreviewGroup>
          ) : null}
        </div>
      ),
    },
    {
      title: '操作',
      key: 'mobile_action',
      width: 80,
      render: (_: unknown, row: FeedbackItem) => (
        <PermGuard perm="feedback:review">
          <Button type="link" size="small" onClick={() => openDetail(row)}>
            审阅
          </Button>
        </PermGuard>
      ),
    },
    {
      title: '联系方式',
      dataIndex: 'contact',
      key: 'contact',
      width: 120,
      render: (v: string | null) =>
        v ? (
          <Typography.Text copyable={{ text: v }} style={{ fontSize: 13 }}>
            {v}
          </Typography.Text>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (_: unknown, row: FeedbackItem) => {
        const tag = (
          <Tag color={STATUS_COLOR[row.status] ?? 'default'} style={{ margin: 0 }}>
            {row.status_label || row.status}
          </Tag>
        )
        // 内部备注在列表里不占一列（多数为空），挂到状态标签的 tooltip 上
        return row.admin_note ? (
          <Tooltip title={`内部备注：${row.admin_note}`}>{tag}</Tooltip>
        ) : (
          tag
        )
      },
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 120,
      responsive: ['md'],
      render: (v: string | null) => <span className="adm-muted">{formatTime(v)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      fixed: 'right',
      render: (_: unknown, row: FeedbackItem) => (
        <Space size={0}>
          <PermGuard perm="feedback:review">
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openDetail(row)}>
              审阅
            </Button>
          </PermGuard>
          {row.status !== 'resolved' && (
            <PermGuard perm="feedback:review">
              <Button type="link" size="small" onClick={() => handleQuickResolve(row)}>
                标记解决
              </Button>
            </PermGuard>
          )}
          {/* handleDelete（useCrudList）内部已弹 Modal.confirm，此处直接调用避免双重确认 */}
          <PermGuard perm="feedback:review">
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDelete(row.id)}
            >
              删除
            </Button>
          </PermGuard>
        </Space>
      ),
    },
  ]

  return (
    <div className="adm-page adm-page--responsive">
      <PageHeader
        icon={<CommentOutlined />}
        title="用户反馈"
        subtitle="用户在前台右侧工具栏提交，后台审阅处理"
        count={{ total: total }}
        actions={
          <>
            <Input.Search
              allowClear
              placeholder="搜索反馈内容"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onSearch={(v) => {
                setKeyword(v.trim())
                setPage(1)
              }}
              style={{ width: 220 }}
            />
            <Tooltip title="重新拉取当前条件下的反馈">
              <Button icon={<ReloadOutlined />} onClick={refresh}>
                刷新
              </Button>
            </Tooltip>

          </>
        }
      />

      {/* 工具条：状态 + 提交时间区间 */}
      <ListToolbar
        hasFilter={hasFilter}
        onReset={handleResetFilters}
        extra={(
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            时间按本地时区取整日，查询时自动换算为库内 UTC
          </Typography.Text>
        )}
      >
        <Segmented
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(String(v))
            setPage(1)
          }}
          options={[{ value: '', label: '全部' }, ...statusOptions]}
        />
        <DatePicker.RangePicker
          value={range}
          onChange={(dates) => {
            setRange(dates && dates[0] && dates[1] ? [dates[0], dates[1]] : null)
            setPage(1)
          }}
          placeholder={['提交开始日期', '提交结束日期']}
          allowClear
        />
      </ListToolbar>

      <DataTable<FeedbackItem>
        dataSource={items}
        columns={columns.filter((column) => {
          if (!column || typeof column !== 'object' || !('key' in column)) return true
          if (column.key === 'mobile_action') return isMobile
          if (column.key === 'action') return !isMobile
          return true
        })}
        rowKey="id"
        loading={loading}
        scrollX={isMobile ? 360 : 1130}
        pagination={{ page: page, pageSize: pageSize, total: total, onPageChange: setPage }}
        empty={{ hasFilter: hasFilter, filteredText: "没有符合筛选条件的反馈", emptyText: "暂无用户反馈", onReset: handleResetFilters }}
      />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：状态流转为 待处理 → 处理中 → 已解决 / 已关闭；「管理员备注」仅内部可见，
        状态与备注的每次变更都会写入操作审计。
      </Typography.Text>

      {/* 审阅弹框：查看全文 + 更新状态 / 管理员备注。
          不用 destroyOnHidden 且内容常驻渲染：openDetail 里的 setFieldsValue
          在弹框关闭状态下调用也能连上已挂载的 Form，避免 useForm 未连接告警 */}
      <Modal
        title={`反馈详情 #${detail?.id ?? ''}`}
        open={!!detail}
        onOk={handleSave}
        onCancel={() => setDetail(null)}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        width={640}
        forceRender
      >
        <div>
          <Space orientation="vertical" size={4} style={{ width: '100%', marginBottom: 12 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              提交人：
              {detail
                ? `${detail.user_nickname || detail.user_account || `用户 ${detail.user_id}`}${
                    detail.user_nickname && detail.user_account ? `（${detail.user_account}）` : ''
                  }`
                : '—'}
              {' · '}提交时间：{formatTime(detail?.created_at ?? null)}
            </Typography.Text>
            {detail?.contact && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                联系方式：<Typography.Text copyable={{ text: detail.contact }} style={{ fontSize: 12 }}>{detail.contact}</Typography.Text>
              </Typography.Text>
            )}
          </Space>
          <div className="fb-content">{detail?.content ?? ''}</div>
          {detail?.images?.length ? (
            <Image.PreviewGroup>
              <Space size={8} wrap style={{ marginBottom: 16 }}>
                {detail.images.map((url) => (
                  <Image
                    key={url}
                    src={url}
                    width={96}
                    height={96}
                    style={{ objectFit: 'cover', borderRadius: "var(--app-radius)" }}
                    alt="反馈截图"
                  />
                ))}
              </Space>
            </Image.PreviewGroup>
          ) : null}
          <Form form={form} layout="vertical">
            <Form.Item name="status" label="处理状态" rules={[{ required: true, message: '请选择状态' }]}>
              <Select options={statusOptions} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="admin_note" label="管理员备注（内部，用户不可见）">
              <Input.TextArea
                placeholder="记录处理过程 / 结论，便于团队协作"
                autoSize={{ minRows: 3, maxRows: 8 }}
                maxLength={2000}
                showCount
              />
            </Form.Item>
          </Form>
        </div>
      </Modal>
    </div>
  )
}
