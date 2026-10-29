import { useCallback, useEffect, useState } from 'react'
import { Button, Form, Input, Modal, Radio, Segmented, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { CheckCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiGet, apiPut } from '../../api/client'
import { useCrudList } from '../../hooks/useCrudList'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { formatTime } from '../../utils/datetime'
import type { AuthRequest } from '../../types'

import { toast } from '../../utils/toast'
import PageHeader from '../../components/ui/PageHeader'
import { DataTable, ListToolbar } from '../../components/ui'
/**
 * 实名审核（知识星球认证）：通过后按所选时长开通 VIP（在现有剩余时长上叠加）。
 *
 * ⚠️ 状态与 VIP 时长的中文名、以及筛选项/时长候选项**全部取接口返回值**，
 * 本文件不保留「代码 → 中文名」的映射表 —— 理由见 `app/models/auth_request.py`。
 * 这里只保留**颜色**映射（纯展示，未知值回落 default）。
 */

/** 状态 → 标签配色（纯展示） */
const STATUS_COLOR: Record<string, string> = {
  pending: 'orange',
  approved: 'green',
  rejected: 'red',
}

interface Option {
  value: string
  label: string
}

interface ReviewValues {
  decision: 'approve' | 'reject'
  vip_duration?: string
  reason?: string
}

export default function AuthRequestsList() {
  const [statusFilter, setStatusFilter] = useState('pending')
  const [statusOptions, setStatusOptions] = useState<Option[]>([])
  const [vipDurationOptions, setVipDurationOptions] = useState<Option[]>([])

  const [active, setActive] = useState<AuthRequest | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<ReviewValues>()
  const decision = Form.useWatch('decision', form)

  const listParams: Record<string, string> = {}
  if (statusFilter) listParams.status = statusFilter

  const { items, loading, page, pageSize, total, refresh, setPage } = useCrudList<AuthRequest>({
    basePath: '/admin/auth-requests',
    params: Object.keys(listParams).length ? listParams : undefined,
  })

  // 筛选项与中文名都来自接口：后端加状态/时长时这里自动跟上
  const loadOptions = useCallback(async () => {
    try {
      const data = await apiGet<{ status_options?: Option[]; vip_duration_options?: Option[] }>(
        '/admin/auth-requests',
        { params: { page: 1, page_size: 1 } },
      )
      setStatusOptions(data.status_options || [])
      setVipDurationOptions(data.vip_duration_options || [])
    } catch {
      // 候选取不到不影响列表本身，静默降级
    }
  }, [])

  useEffect(() => {
    loadOptions()
  }, [loadOptions])

  const openReview = (row: AuthRequest) => {
    setActive(row)
    form.setFieldsValue({ decision: 'approve', vip_duration: vipDurationOptions[0]?.value, reason: '' })
  }

  const handleSubmit = async () => {
    if (!active) return
    let values: ReviewValues
    try {
      values = await form.validateFields()
    } catch (err) {
      if (isFormValidationError(err)) return
      throw err
    }
    setSaving(true)
    try {
      if (values.decision === 'approve') {
        await apiPut(`/admin/auth-requests/${active.id}/approve`, {
          vip_duration: values.vip_duration,
        })
        toast.success('已通过认证')
      } else {
        await apiPut(`/admin/auth-requests/${active.id}/reject`, {
          reason: values.reason?.trim() || '不符合要求',
        })
        toast.success('已驳回')
      }
      setActive(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setSaving(false)
    }
  }

  const columns: TableProps<AuthRequest>['columns'] = [
    {
      title: '账号',
      dataIndex: 'account',
      key: 'account',
      width: 180,
      render: (v: string | null) => <span className="adm-cell__name">{v || '—'}</span>,
    },
    {
      title: '星球号',
      dataIndex: 'star_code',
      key: 'star_code',
      width: 140,
      render: (v: string) => (
        <Typography.Text code style={{ fontSize: 12 }}>
          {v}
        </Typography.Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (_: unknown, row: AuthRequest) => {
        const tag = (
          <Tag color={STATUS_COLOR[row.status ?? ''] ?? 'default'} style={{ margin: 0 }}>
            {row.status_label || row.status}
          </Tag>
        )
        // 驳回原因不占一列（只有驳回行才有），挂到状态标签的 tooltip 上
        return row.reject_reason ? <Tooltip title={`驳回原因：${row.reject_reason}`}>{tag}</Tooltip> : tag
      },
    },
    {
      title: 'VIP 时长',
      dataIndex: 'vip_duration',
      key: 'vip_duration',
      width: 110,
      render: (_: unknown, row: AuthRequest) =>
        row.vip_duration ? (
          <span>{row.vip_duration_label || row.vip_duration}</span>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '审核人',
      dataIndex: 'reviewer_account',
      key: 'reviewer_account',
      width: 170,
      render: (_: unknown, row: AuthRequest) => {
        if (row.reviewed_by == null) return <span className="adm-muted">—</span>
        const label = row.reviewer_account || `#${row.reviewed_by}`
        return (
          <Typography.Text ellipsis={{ tooltip: label }} style={{ maxWidth: 150 }}>
            {label}
          </Typography.Text>
        )
      },
    },
    {
      title: '审核时间',
      dataIndex: 'reviewed_at',
      key: 'reviewed_at',
      width: 140,
      render: (v: string | null) => <span className="adm-muted">{v ? formatTime(v) : '—'}</span>,
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 140,
      render: (v: string | null) => <span className="adm-muted">{formatTime(v)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      fixed: 'right',
      render: (_: unknown, row: AuthRequest) =>
        row.status === 'pending' ? (
          <PermGuard perm="auth_request:review">
            <Button type="link" size="small" onClick={() => openReview(row)}>
              审核
            </Button>
          </PermGuard>
        ) : (
          <span className="adm-muted">已完成</span>
        ),
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<CheckCircleOutlined />}
        title="实名审核"
        subtitle="知识星球认证申请；通过后按所选时长开通 VIP（在现有剩余时长上叠加）"
        count={{ total: total }}
        actions={
          <>
            <Tooltip title="重新拉取当前条件下的申请">
              <Button icon={<ReloadOutlined />} onClick={refresh}>
                刷新
              </Button>
            </Tooltip>

          </>
        }
      />

      <ListToolbar
        hasFilter={!!statusFilter}
        onReset={() => setStatusFilter('pending')}
        extra={(
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            按提交时间正序（先到先审）；审批过的记录可在「已通过 / 已驳回」里回查
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
      </ListToolbar>

      <DataTable<AuthRequest>
        dataSource={items}
        columns={columns}
        rowKey="id"
        loading={loading}
        scrollX={1090}
        pagination={{ page: page, pageSize: pageSize, total: total, onPageChange: setPage }}
        empty={{
        hasFilter: !!statusFilter,
        filteredText: '没有符合筛选条件的认证申请',
        emptyText: '暂无待处理的认证申请',
        onReset: () => setStatusFilter('pending'),
        }}
      />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：通过认证会在用户<b>现有剩余时长上叠加</b>（不会覆盖），避免已持有长期 VIP 的用户换绑星球号时被缩短；
        驳回原因对用户可见，请写成用户能看懂的话。两种处置都会写入操作审计。
      </Typography.Text>

      <Modal
        title={`审核认证申请 #${active?.id ?? ''}`}
        open={!!active}
        onOk={handleSubmit}
        onCancel={() => setActive(null)}
        okText="提交"
        cancelText="取消"
        confirmLoading={saving}
        width={520}
        forceRender
      >
        {active && (
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            申请人：{active.account || `用户 ${active.user_id}`} · 星球号：
            {active.star_code} · 提交时间：{formatTime(active.created_at)}
          </Typography.Paragraph>
        )}
        <Form form={form} layout="vertical">
          <Form.Item name="decision" label="审核结论" rules={[{ required: true, message: '请选择结论' }]}>
            <Radio.Group
              optionType="button"
              buttonStyle="solid"
              options={[
                { value: 'approve', label: '通过' },
                { value: 'reject', label: '驳回' },
              ]}
            />
          </Form.Item>
          {decision === 'reject' ? (
            <Form.Item
              name="reason"
              label="驳回原因（用户可见）"
              rules={[{ required: true, message: '请填写驳回原因' }]}
              style={{ marginBottom: 0 }}
            >
              <Input.TextArea rows={3} maxLength={500} showCount placeholder="如：星球号填写有误，请核对后重新提交" />
            </Form.Item>
          ) : (
            <Form.Item
              name="vip_duration"
              label="开通时长"
              rules={[{ required: true, message: '请选择开通时长' }]}
              extra="在用户现有剩余时长上叠加，不会覆盖"
              style={{ marginBottom: 0 }}
            >
              <Radio.Group options={vipDurationOptions} />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}
