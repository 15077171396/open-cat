import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Card, Empty, Form, Grid, Input, Modal, Segmented, Select, Space, Table, Tabs, Tag, Tooltip, Typography } from 'antd'
import type { FormInstance, TableProps } from 'antd'
import { FlagOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useSearchParams } from 'react-router-dom'
import { apiGet, apiPut } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { formatTime } from '../../utils/datetime'

import { toast } from '../../utils/toast'
import PageHeader from '../../components/ui/PageHeader'
import { DataTable, ListToolbar } from '../../components/ui'
/**
 * 内容审核中心：机审留痕（纠正误报）与用户举报（处理）放同一页两个 Tab，
 * 两者看的是同一批 UGC 的不合规信号。查看与处理动作权限分开控制。
 *
 * ⚠️ 内容类型 / 状态 / 机器处置的**中文名一律取接口返回的 `*_label`**，
 * 筛选下拉取接口的 `content_type_options` / `status_options` —— 本文件不保留任何
 * 「代码 → 中文名」的映射表。原因见 `app/models/moderation.py` 的注释：
 * 后端加了 `comment` 类型而管理端没跟，审核台的「内容类型」列曾长期显示成裸代码 `comment`。
 * 这里只保留**颜色**映射（纯展示，未知值回落 default，不影响正确性）。
 */

/** 状态 → 标签配色（两个 Tab 共用；颜色是纯展示，未知值回落 default） */
const STATUS_COLOR: Record<string, string> = {
  pending: 'orange',
  approved: 'green',
  rejected: 'red',
  resolved: 'green',
  dismissed: 'default',
}

/** 机器处置 → 标签配色 */
const ACTION_COLOR: Record<string, string> = {
  reject: 'red',
  mask: 'blue',
  review: 'orange',
}

interface Option {
  value: string
  label: string
}

interface ModerationLogItem {
  id: number
  content_type: string
  content_type_label: string
  target_id: string | null
  user_id: number | null
  excerpt: string | null
  hit_words: string[]
  action: string
  action_label: string
  status: string
  status_label: string
  reviewer_id: number | null
  reviewed_at: string | null
  review_note: string | null
  created_at: string | null
}

interface ReportItem {
  id: number
  reporter_id: number
  content_type: string
  content_type_label: string
  target_id: string
  reason: string
  reason_label: string
  detail: string | null
  status: string
  status_label: string
  handler_id: number | null
  handled_at: string | null
  handle_note: string | null
  created_at: string | null
}

/** 复核 / 处理弹框（两个 Tab 共用，仅选项与提交逻辑不同） */
interface ReviewModalProps {
  title: string
  open: boolean
  saving: boolean
  form: FormInstance
  statusOptions: { value: string; label: string }[]
  hint?: React.ReactNode
  onOk: () => void
  onCancel: () => void
}

function ReviewModal({ title, open, saving, form, statusOptions, hint, onOk, onCancel }: ReviewModalProps) {
  return (
    <Modal
      title={title}
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      okText="提交"
      cancelText="取消"
      confirmLoading={saving}
      width={560}
      forceRender
    >
      {hint}
      <Form form={form} layout="vertical">
        <Form.Item name="status" label="处理结论" rules={[{ required: true, message: '请选择处理结论' }]}>
          <Select placeholder="请选择" options={statusOptions} />
        </Form.Item>
        <Form.Item name="note" label="备注（内部可见）">
          <Input.TextArea
            placeholder="记录判断依据，便于后续复盘与团队协作"
            autoSize={{ minRows: 3, maxRows: 8 }}
            maxLength={500}
            showCount
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}

/** 长文本单元格：超两行省略，hover 出全文 */
function textCell(v: string | null, maxWidth = 300) {
  return v ? (
    <Typography.Paragraph
      ellipsis={{ tooltip: v, rows: 2 }}
      style={{ marginBottom: 0, maxWidth, whiteSpace: 'pre-wrap' }}
    >
      {v}
    </Typography.Paragraph>
  ) : (
    <span className="adm-muted">—</span>
  )
}

function LogTab({ onChanged }: { onChanged: () => void }) {
  const screens = Grid.useBreakpoint()
  const isMobile = screens.md === false
  const [statusFilter, setStatusFilter] = useState('')
  const [contentType, setContentType] = useState('')
  const [contentTypeOptions, setContentTypeOptions] = useState<Option[]>([])
  const [statusOptions, setStatusOptions] = useState<Option[]>([])
  const [active, setActive] = useState<ModerationLogItem | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const listParams: Record<string, string> = {}
  if (statusFilter) listParams.status = statusFilter
  if (contentType) listParams.content_type = contentType

  const { items, loading, page, pageSize, total, refresh, setPage } = useCrudList<ModerationLogItem>({
    basePath: '/admin/moderation/logs',
    params: Object.keys(listParams).length ? listParams : undefined,
  })

  // 筛选项与中文名都来自接口：后端加内容类型时这里自动跟上
  const loadOptions = useCallback(async () => {
    try {
      const data = await apiGet<{ content_type_options?: Option[]; status_options?: Option[] }>(
        '/admin/moderation/logs',
        { params: { page: 1, page_size: 1 } },
      )
      setContentTypeOptions(data.content_type_options || [])
      setStatusOptions(data.status_options || [])
    } catch {
      // 下拉候选取不到不影响列表本身，静默降级
    }
  }, [])

  useEffect(() => {
    loadOptions()
  }, [loadOptions])

  const hasFilter = !!statusFilter || !!contentType

  const openModal = (row: ModerationLogItem) => {
    setActive(row)
    form.setFieldsValue({ status: undefined, note: '' })
  }

  const handleOk = async () => {
    if (!active) return
    try {
      const values = await form.validateFields()
      setSaving(true)
      await apiPut(`/admin/moderation/logs/${active.id}`, {
        status: values.status,
        note: values.note?.trim() || null,
      })
      toast.success('已提交复核结果')
      setActive(null)
      refresh()
      onChanged()
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setSaving(false)
    }
  }

  const columns: TableProps<ModerationLogItem>['columns'] = [
    {
      title: '内容类型',
      dataIndex: 'content_type',
      key: 'content_type',
      width: 90,
      responsive: ['sm'],
      render: (_: unknown, row: ModerationLogItem) => (
        <Tag color="geekblue" style={{ margin: 0 }}>
          {row.content_type_label || row.content_type}
        </Tag>
      ),
    },
    {
      title: '命中词',
      dataIndex: 'hit_words',
      key: 'hit_words',
      width: 170,
      responsive: ['lg'],
      render: (words: string[]) =>
        words.length ? (
          <Space size={4} wrap>
            {words.map((w) => (
              <Tag key={w} color="volcano" style={{ margin: 0 }}>
                {w}
              </Tag>
            ))}
          </Space>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '内容摘要',
      dataIndex: 'excerpt',
      key: 'excerpt',
      width: 300,
      render: (v: string | null) => textCell(v),
    },
    {
      title: '机器处置',
      dataIndex: 'action',
      key: 'action',
      width: 100,
      responsive: ['md'],
      render: (_: unknown, row: ModerationLogItem) => (
        <Tag color={ACTION_COLOR[row.action] ?? 'default'} style={{ margin: 0 }}>
          {row.action_label || row.action}
        </Tag>
      ),
    },
    {
      title: '复核状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (_: unknown, row: ModerationLogItem) => {
        const tip =
          row.reviewer_id != null
            ? `复核人 #${row.reviewer_id}${row.reviewed_at ? ` · ${formatTime(row.reviewed_at)}` : ''}${
                row.review_note ? ` · ${row.review_note}` : ''
              }`
            : undefined
        const tag = (
          <Tag color={STATUS_COLOR[row.status] ?? 'default'} style={{ margin: 0 }}>
            {row.status_label || row.status}
          </Tag>
        )
        return tip ? <Tooltip title={tip}>{tag}</Tooltip> : tag
      },
    },
    {
      title: '作者',
      dataIndex: 'user_id',
      key: 'user_id',
      width: 90,
      responsive: ['lg'],
      render: (v: number | null) => (v != null ? <span className="adm-num">{v}</span> : <span className="adm-muted">—</span>),
    },
    {
      title: '复核人',
      dataIndex: 'reviewer_id',
      key: 'reviewer_id',
      width: 90,
      responsive: ['lg'],
      render: (v: number | null) =>
        v != null ? <span className="adm-num">{v}</span> : <span className="adm-muted">—</span>,
    },
    {
      title: '命中时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 130,
      responsive: ['md'],
      render: (v: string | null) => <span className="adm-muted">{formatTime(v)}</span>,
    },
    {
      title: '操作',
      key: 'action_col',
      width: 80,
      fixed: isMobile ? undefined : 'right',
      render: (_: unknown, row: ModerationLogItem) => (
        <PermGuard perm="moderation:review">
          <Button type="link" size="small" onClick={() => openModal(row)}>
            复核
          </Button>
        </PermGuard>
      ),
    },
  ]

  return (
    <div>
      <ListToolbar
        hasFilter={hasFilter}
        onReset={() => { setStatusFilter(''); setContentType('') }}
        extra={(
          <Tooltip title="重新拉取当前条件下的留痕">
            <Button icon={<ReloadOutlined />} onClick={refresh}>
              刷新
            </Button>
          </Tooltip>
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
        <Select
          allowClear
          placeholder="全部内容类型"
          value={contentType || undefined}
          onChange={(v) => {
            setContentType(v ?? '')
            setPage(1)
          }}
          options={contentTypeOptions}
          style={{ width: 150 }}
        />
      </ListToolbar>

      <DataTable<ModerationLogItem>
        dataSource={items}
        columns={columns}
        rowKey="id"
        loading={loading}
        scrollX={isMobile ? 480 : 1150}
        pagination={{ page: page, pageSize: pageSize, total: total, onPageChange: setPage }}
        empty={{
        hasFilter: hasFilter,
        filteredText: '没有符合筛选条件的审核记录',
        emptyText: '暂无机器审核记录',
        }}
      />

      <ReviewModal
        title={`复核机审判定 #${active?.id ?? ''}`}
        open={!!active}
        saving={saving}
        form={form}
        statusOptions={[
          { value: 'approved', label: '误报，放行' },
          { value: 'rejected', label: '维持拦截' },
        ]}
        hint={
          active && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              命中词：{active.hit_words.join('、') || '—'} · 机器处置：
              {active.action_label || active.action}
              {active.review_note ? ` · 上次备注：${active.review_note}` : ''}
            </Typography.Paragraph>
          )
        }
        onOk={handleOk}
        onCancel={() => setActive(null)}
      />
    </div>
  )
}

function ReportTab({ onChanged }: { onChanged: () => void }) {
  const screens = Grid.useBreakpoint()
  const isMobile = screens.md === false
  const [statusFilter, setStatusFilter] = useState('')
  const [contentType, setContentType] = useState('')
  const [contentTypeOptions, setContentTypeOptions] = useState<Option[]>([])
  const [statusOptions, setStatusOptions] = useState<Option[]>([])
  const [active, setActive] = useState<ReportItem | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const listParams: Record<string, string> = {}
  if (statusFilter) listParams.status = statusFilter
  if (contentType) listParams.content_type = contentType

  const { items, loading, page, pageSize, total, refresh, setPage } = useCrudList<ReportItem>({
    basePath: '/admin/reports',
    params: Object.keys(listParams).length ? listParams : undefined,
  })

  const loadOptions = useCallback(async () => {
    try {
      const data = await apiGet<{ content_type_options?: Option[]; status_options?: Option[] }>(
        '/admin/reports',
        { params: { page: 1, page_size: 1 } },
      )
      setContentTypeOptions(data.content_type_options || [])
      setStatusOptions(data.status_options || [])
    } catch {
      // 同上：下拉候选取不到不影响列表
    }
  }, [])

  useEffect(() => {
    loadOptions()
  }, [loadOptions])

  const hasFilter = !!statusFilter || !!contentType

  const openModal = (row: ReportItem) => {
    setActive(row)
    form.setFieldsValue({ status: undefined, note: '' })
  }

  const handleOk = async () => {
    if (!active) return
    try {
      const values = await form.validateFields()
      setSaving(true)
      await apiPut(`/admin/reports/${active.id}`, {
        status: values.status,
        note: values.note?.trim() || null,
      })
      toast.success('举报已处理')
      setActive(null)
      refresh()
      onChanged()
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setSaving(false)
    }
  }

  const columns: TableProps<ReportItem>['columns'] = [
    {
      title: '内容类型',
      dataIndex: 'content_type',
      key: 'content_type',
      width: 90,
      render: (_: unknown, row: ReportItem) => (
        <Tag color="geekblue" style={{ margin: 0 }}>
          {row.content_type_label || row.content_type}
        </Tag>
      ),
    },
    {
      title: '举报对象',
      dataIndex: 'target_id',
      key: 'target_id',
      width: 130,
      responsive: ['sm'],
      render: (v: string) => (
        <Typography.Text copyable={{ text: v }} style={{ fontSize: 13 }}>
          {v}
        </Typography.Text>
      ),
    },
    {
      title: '举报人',
      dataIndex: 'reporter_id',
      key: 'reporter_id',
      width: 90,
      responsive: ['lg'],
      render: (v: number) => <span className="adm-num">{v}</span>,
    },
    {
      title: '理由',
      dataIndex: 'reason_label',
      key: 'reason_label',
      width: 100,
      render: (_: unknown, row: ReportItem) => (
        <Tag color="orange" style={{ margin: 0 }}>
          {row.reason_label || row.reason}
        </Tag>
      ),
    },
    {
      title: '补充说明',
      dataIndex: 'detail',
      key: 'detail',
      width: 300,
      render: (v: string | null) => textCell(v),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (_: unknown, row: ReportItem) => {
        const tip =
          row.handler_id != null
            ? `处理人 #${row.handler_id}${row.handled_at ? ` · ${formatTime(row.handled_at)}` : ''}${
                row.handle_note ? ` · ${row.handle_note}` : ''
              }`
            : undefined
        const tag = (
          <Tag color={STATUS_COLOR[row.status] ?? 'default'} style={{ margin: 0 }}>
            {row.status_label || row.status}
          </Tag>
        )
        return tip ? <Tooltip title={tip}>{tag}</Tooltip> : tag
      },
    },
    {
      title: '处理人',
      dataIndex: 'handler_id',
      key: 'handler_id',
      width: 90,
      responsive: ['lg'],
      render: (v: number | null) =>
        v != null ? <span className="adm-num">{v}</span> : <span className="adm-muted">—</span>,
    },
    {
      title: '举报时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 130,
      render: (v: string | null) => <span className="adm-muted">{formatTime(v)}</span>,
    },
    {
      title: '操作',
      key: 'action_col',
      width: 80,
      fixed: isMobile ? undefined : 'right',
      render: (_: unknown, row: ReportItem) =>
        row.status === 'pending' ? (
          <PermGuard perm="report:handle">
            <Button type="link" size="small" onClick={() => openModal(row)}>
              处理
            </Button>
          </PermGuard>
        ) : (
          <span className="adm-muted">已完成</span>
        ),
    },
  ]

  return (
    <div>
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Segmented
              value={statusFilter}
              onChange={(v) => {
                setStatusFilter(String(v))
                setPage(1)
              }}
              options={[{ value: '', label: '全部' }, ...statusOptions]}
            />
            <Select
              allowClear
              placeholder="全部内容类型"
              value={contentType || undefined}
              onChange={(v) => {
                setContentType(v ?? '')
                setPage(1)
              }}
              options={contentTypeOptions}
              style={{ width: 150 }}
            />
            {hasFilter && (
              <Tooltip title="清空筛选条件">
                <Button
                  type="text"
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={() => {
                    setStatusFilter('')
                    setContentType('')
                    setPage(1)
                  }}
                >
                  重置
                </Button>
              </Tooltip>
            )}
          </Space>
          <Tooltip title="重新拉取当前条件下的举报">
            <Button icon={<ReloadOutlined />} onClick={refresh}>
              刷新
            </Button>
          </Tooltip>
        </div>
      </Card>

      <Card className="adm-tablecard" variant="outlined">
        <Table<ReportItem>
          className="adm-table"
          dataSource={items}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: isMobile ? 570 : 1100 }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={hasFilter ? '没有符合筛选条件的举报' : '暂无用户举报'}
              />
            ),
          }}
          pagination={{
            current: page,
            pageSize,
            total,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
            showSizeChanger: false,
          }}
        />
      </Card>

      <ReviewModal
        title={`处理举报 #${active?.id ?? ''}`}
        open={!!active}
        saving={saving}
        form={form}
        statusOptions={[
          { value: 'resolved', label: '已处理' },
          { value: 'dismissed', label: '判定为误报，驳回' },
        ]}
        hint={
          active && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              理由：{active.reason_label || active.reason}
              {active.detail ? ` · 补充：${active.detail}` : ''}
            </Typography.Paragraph>
          )
        }
        onOk={handleOk}
        onCancel={() => setActive(null)}
      />
    </div>
  )
}

export default function ModerationList() {
  // 角标仅提示不作为准入依据（数量为 0 也能进页面查历史）；拉取失败静默降级
  const [summary, setSummary] = useState({ pending_logs: 0, pending_reports: 0 })
  // Tab 与 URL 同步：顶栏待办可以直接深链到「用户举报」而不是只落到默认 Tab
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') === 'reports' ? 'reports' : 'logs'

  const changeTab = (key: string) => {
    const next = new URLSearchParams(searchParams)
    if (key === 'reports') next.set('tab', 'reports')
    else next.delete('tab')
    // replace：切 Tab 不该在浏览器历史里留下一步，否则「返回」会变成在两 Tab 间来回跳
    setSearchParams(next, { replace: true })
  }

  const refreshSummary = useCallback(() => {
    apiGet<{ pending_logs: number; pending_reports: number }>('/admin/moderation/summary')
      .then((data) => data && setSummary(data))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    refreshSummary()
  }, [refreshSummary])

  const tabItems = [
    {
      key: 'logs',
      label: (
        <Space size={6}>
          机审留痕
          <Badge count={summary.pending_logs} size="small" overflowCount={99} />
        </Space>
      ),
      // 处理完必须回头刷新角标：否则复核掉一条后 Tab 上还挂着旧数字，
      // 审核者会以为队列里还有活儿
      children: <LogTab onChanged={refreshSummary} />,
    },
    {
      key: 'reports',
      label: (
        <Space size={6}>
          用户举报
          <Badge count={summary.pending_reports} size="small" overflowCount={99} />
        </Space>
      ),
      children: <ReportTab onChanged={refreshSummary} />,
    },
  ]

  return (
    <div className="adm-page adm-page--responsive">
      <PageHeader
        icon={<SafetyCertificateOutlined />}
        title="内容审核"
        subtitle={[
            "机审判定留痕与用户举报在同一处处理",
            <>待复核 <strong>{summary.pending_logs}</strong> 条 · 待处理举报{' '}
                <strong>{summary.pending_reports}</strong> 条</>,
          ]}
      />

      <Tabs activeKey={tab} onChange={changeTab} items={tabItems} />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        <FlagOutlined /> 敏感词库由 <code>MODERATION_EXTRA_WORDS</code> 维护（部署配置，非数据库），
        改后需重启后端生效；复核与处理动作均写入操作审计。
      </Typography.Text>
    </div>
  )
}
