import { useCallback, useEffect, useState } from 'react'
import { Button, Form, Input, Modal, Select, Space, Switch, Tag, Typography } from 'antd'
import { ApiOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import { DataTable, ModalForm, PageHeader, StatusTag } from '../../components/ui'
import { PermGuard } from '../../components/PermGuard'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { ensureElevation, invalidateElevation } from '../../utils/elevation'
import { toast } from '../../utils/toast'

/**
 * Webhook 订阅（/webhooks）：内容事件的出站推送（教程发布 / 下架）。
 *
 * 运维口径：订阅断了不可怕，看不见才可怕 —— 每行直接暴露最近投递状态与
 * 连续失败次数（failure_streak），「测试」按钮真实走一次投递（写审计）。
 */

interface WebhookItem {
  id: number
  name: string
  url: string
  events: string[]
  active: boolean
  created_at: string | null
  last_status: string | null
  last_event: string | null
  last_delivered_at: string | null
  last_error: string | null
  failure_streak: number
}

const EVENT_OPTIONS = [
  { value: 'tutorial.published', label: '教程发布' },
  { value: 'tutorial.unpublished', label: '教程下架' },
]

interface CreatedWebhook {
  id: number
  name: string
  url: string
  events: string[]
  secret: string
}

export default function WebhooksPage() {
  const [items, setItems] = useState<WebhookItem[]>([])
  const [loading, setLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [created, setCreated] = useState<CreatedWebhook | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<{ items: WebhookItem[] }>('/admin/webhooks')
      setItems(data.items)
    } catch (error) {
      toast.error(getErrorMessage(error, 'Webhook 订阅加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const handleCreate = async (raw: Record<string, unknown>) => {
    try {
      const data = await apiPost<CreatedWebhook>('/admin/webhooks', {
        name: String(raw.name ?? '').trim(),
        url: String(raw.url ?? '').trim(),
        events: Array.isArray(raw.events) && raw.events.length ? (raw.events as string[]) : undefined,
      })
      setCreated(data)
      setCreateOpen(false)
      void load()
    } catch (error) {
      toast.error(getErrorMessage(error, '创建失败'))
    }
  }

  const handleToggle = async (row: WebhookItem, active: boolean) => {
    try {
      await apiPut(`/admin/webhooks/${row.id}`, { active })
      toast.success(active ? '已启用' : '已停用')
      void load()
    } catch (error) {
      toast.error(getErrorMessage(error, '操作失败'))
    }
  }

  const handleTest = (row: WebhookItem) => {
    Modal.confirm({
      title: `向「${row.name}」试投一次？`,
      content: '会真实发起一次 HTTP 投递（写审计），用于验证 URL 可达与验签正确。',
      okText: '试投',
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await apiPost<{ ok: boolean; error: string }>(`/admin/webhooks/${row.id}/test`)
          if (result.ok) {
            toast.success('试投成功：对方返回 2xx')
          } else {
            toast.error(`试投失败：${result.error || '未知原因'}`)
          }
          void load()
        } catch (error) {
          toast.error(getErrorMessage(error, '试投失败'))
        }
      },
    })
  }

  const handleDelete = (row: WebhookItem) => {
    Modal.confirm({
      title: `删除订阅「${row.name}」？`,
      content: '删除后该地址不再收到任何事件推送（无法恢复）。确认对方不再需要这些事件。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          // S5 危险操作二次校验：删除即静默断流，先重输密码换短时凭据
          const elevationToken = await ensureElevation()
          await apiDelete(`/admin/webhooks/${row.id}`, {
            headers: { 'X-Elevation-Token': elevationToken },
          })
          toast.success('已删除')
          void load()
        } catch (error) {
          if (String(getErrorMessage(error, '')).includes('二次校验')) {
            invalidateElevation()
          }
          toast.error(getErrorMessage(error, '删除失败'))
        }
      },
    })
  }

  const columns: ColumnsType<WebhookItem> = [
    { title: '名称', dataIndex: 'name', width: 160, ellipsis: true },
    {
      title: '回调地址',
      dataIndex: 'url',
      width: 260,
      ellipsis: true,
      render: (v: string) => <Typography.Text code>{v}</Typography.Text>,
    },
    {
      title: '订阅事件',
      dataIndex: 'events',
      width: 180,
      render: (events: string[]) =>
        events?.length === EVENT_OPTIONS.length || !events?.length ? (
          <Tag>全部事件</Tag>
        ) : (
          events.map((e) => <Tag key={e}>{EVENT_OPTIONS.find((o) => o.value === e)?.label || e}</Tag>)
        ),
    },
    {
      title: '启用',
      dataIndex: 'active',
      width: 90,
      render: (_: boolean, row: WebhookItem) => (
        <PermGuard perm="webhook:manage">
          <Switch checked={row.active} onChange={(v) => void handleToggle(row, v)} />
        </PermGuard>
      ),
    },
    {
      title: '最近投递',
      key: 'delivery',
      width: 220,
      render: (_: unknown, row: WebhookItem) => {
        if (!row.last_status) return <Typography.Text type="secondary">从未投递</Typography.Text>
        const ok = row.last_status === 'success'
        return (
          <Space direction="vertical" size={0}>
            <StatusTag tone={ok ? 'success' : 'danger'}>{ok ? '成功' : '失败'}</StatusTag>
            {!ok && row.failure_streak > 0 && (
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                连续失败 {row.failure_streak} 次
              </Typography.Text>
            )}
            {!ok && row.last_error && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                {row.last_error}
              </Typography.Text>
            )}
          </Space>
        )
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_: unknown, row: WebhookItem) => (
        <PermGuard perm="webhook:manage">
          <Space size={4}>
            <Button size="small" onClick={() => handleTest(row)}>
              测试
            </Button>
            <Button size="small" danger onClick={() => handleDelete(row)}>
              删除
            </Button>
          </Space>
        </PermGuard>
      ),
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<ApiOutlined />}
        title="Webhook 订阅"
        subtitle={['内容事件出站推送：教程发布 / 下架实时通知订阅方（HMAC-SHA256 签名可验证）']}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <PermGuard perm="webhook:manage">
              <Button type="primary" onClick={() => setCreateOpen(true)}>
                新增订阅
              </Button>
            </PermGuard>
          </Space>
        }
      />

      <DataTable<WebhookItem>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={{ page: 1, pageSize: 200, total: items.length, onPageChange: () => undefined }}
        empty={{
          hasFilter: false,
          filteredText: '没有符合条件的订阅',
          emptyText: '还没有 Webhook 订阅。点右上角「新增订阅」，让内容事件主动推给你的渲染站。',
        }}
      />

      <ModalForm
        open={createOpen}
        title="新增 Webhook 订阅"
        onCancel={() => setCreateOpen(false)}
        onSubmit={handleCreate}
        okText="创建"
        formStyle={{ marginTop: 16 }}
      >
        <Form.Item
          name="name"
          label="名称"
          rules={[{ required: true, message: '请填写名称' }, { max: 100 }]}
        >
          <Input placeholder="给谁用（如「渲染站」）" />
        </Form.Item>
        <Form.Item
          name="url"
          label="回调地址"
          rules={[
            { required: true, message: '请填写 https 回调地址' },
            { pattern: /^https:\/\/.+/, message: '必须以 https:// 开头（明文 http 会泄露签名密钥）' },
          ]}
        >
          <Input placeholder="https://example.com/hooks/opencat" />
        </Form.Item>
        <Form.Item name="events" label="订阅事件">
          <Select mode="multiple" options={EVENT_OPTIONS} placeholder="留空 = 订阅全部事件" allowClear />
        </Form.Item>
      </ModalForm>

      {/* 签名密钥一次性弹窗：订阅方验签依赖它，之后无法再次查看 */}
      <Modal
        open={created !== null}
        title="订阅已创建 —— 请立即保存签名密钥"
        onCancel={() => setCreated(null)}
        footer={[
          <Button key="done" type="primary" onClick={() => setCreated(null)}>
            我已保存
          </Button>,
        ]}
        width={560}
      >
        {created && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Typography.Paragraph>
              每次投递的请求头 <code>X-OpenCat-Signature</code> 为 HMAC-SHA256(密钥, 请求体)，
              前缀 <code>sha256=</code>。用它验证请求确实来自本站。
            </Typography.Paragraph>
            <Typography.Paragraph code copyable style={{ marginBottom: 0, wordBreak: 'break-all' }}>
              {created.secret}
            </Typography.Paragraph>
            <Typography.Text type="warning">
              该密钥只显示这一次；丢失后请删除本订阅并重建。
            </Typography.Text>
          </Space>
        )}
      </Modal>
    </div>
  )
}
