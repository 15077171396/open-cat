import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Select, Space, Tag, Typography } from 'antd'
import { KeyOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import { DataTable, ModalForm, PageHeader } from '../../components/ui'
import { PermGuard } from '../../components/PermGuard'
import { apiDelete, apiGet, apiPost } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { ensureElevation, invalidateElevation } from '../../utils/elevation'
import { toast } from '../../utils/toast'

/**
 * 访问令牌（PAT，/api-tokens）：机器客户端调内容 API 的长期凭据。
 *
 * 安全口径（与后端 api_token_service 同源）：
 * - 明文令牌**只在签发响应里出现一次**，之后任何接口都拿不回来 —— 丢了只能吊销重建；
 * - 列表只回前缀（oc_pat_xxxxxxxx），认得出是哪把钥匙，但不泄露全文；
 * - 签发 / 吊销都写审计（高危动作）。
 */

interface ApiTokenItem {
  id: number
  name: string
  prefix: string
  scopes: string[]
  created_at: string | null
  expires_at: string | null
  last_used_at: string | null
  revoked: boolean
  expired: boolean
}

const SCOPE_OPTIONS = [{ value: 'content:read', label: '内容 API 只读（content:read）' }]

const EXPIRE_OPTIONS = [
  { value: 0, label: '永不过期' },
  { value: 30, label: '30 天' },
  { value: 90, label: '90 天' },
  { value: 365, label: '365 天' },
]

interface CreatedToken {
  id: number
  token: string
  prefix: string
  scopes: string[]
  expires_at: string | null
}

export default function ApiTokensPage() {
  const [items, setItems] = useState<ApiTokenItem[]>([])
  const [loading, setLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [created, setCreated] = useState<CreatedToken | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<{ items: ApiTokenItem[] }>('/admin/api-tokens')
      setItems(data.items)
    } catch (error) {
      toast.error(getErrorMessage(error, '访问令牌加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const handleCreate = async (raw: Record<string, unknown>) => {
    try {
      const data = await apiPost<CreatedToken>('/admin/api-tokens', {
        name: String(raw.name ?? '').trim(),
        scopes: Array.isArray(raw.scopes) && raw.scopes.length ? (raw.scopes as string[]) : undefined,
        expires_days: Number(raw.expires_days) || undefined,
      })
      setCreated(data)
      setCreateOpen(false)
      void load()
    } catch (error) {
      toast.error(getErrorMessage(error, '签发失败'))
    }
  }

  const handleRevoke = (row: ApiTokenItem) => {
    Modal.confirm({
      title: `确认吊销令牌「${row.name}」？`,
      content: `吊销后使用该令牌（前缀 ${row.prefix}…）的客户端立即 401，且无法恢复 —— 需要重新签发并更新客户端配置。`,
      okText: '吊销',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          // S5 危险操作二次校验：吊销不可逆，先重输密码换短时凭据
          const elevationToken = await ensureElevation()
          await apiDelete(`/admin/api-tokens/${row.id}`, {
            headers: { 'X-Elevation-Token': elevationToken },
          })
          toast.success('已吊销')
          void load()
        } catch (error) {
          if (String(getErrorMessage(error, '')).includes('二次校验')) {
            invalidateElevation()
          }
          toast.error(getErrorMessage(error, '吊销失败'))
        }
      },
    })
  }

  const columns: ColumnsType<ApiTokenItem> = [
    { title: '用途', dataIndex: 'name', width: 200, ellipsis: true },
    {
      title: '令牌前缀',
      dataIndex: 'prefix',
      width: 180,
      render: (v: string) => <Typography.Text code>{v}…</Typography.Text>,
    },
    {
      title: '作用域',
      dataIndex: 'scopes',
      width: 200,
      render: (scopes: string[]) => (scopes?.length ? scopes.map((s) => <Tag key={s}>{s}</Tag>) : '-'),
    },
    {
      title: '状态',
      dataIndex: 'revoked',
      width: 110,
      render: (_: boolean, row: ApiTokenItem) => {
        if (row.revoked) return <Tag color="error">已吊销</Tag>
        if (row.expired) return <Tag color="warning">已过期</Tag>
        return <Tag color="success">有效</Tag>
      },
    },
    {
      title: '最近使用',
      dataIndex: 'last_used_at',
      width: 170,
      render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '从未使用'),
    },
    {
      title: '过期时刻',
      dataIndex: 'expires_at',
      width: 170,
      render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '永不过期'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_: unknown, row: ApiTokenItem) => (
        <PermGuard perm="api_token:manage">
          {row.revoked ? (
            <Typography.Text type="secondary">—</Typography.Text>
          ) : (
            <Button size="small" danger onClick={() => handleRevoke(row)}>
              吊销
            </Button>
          )}
        </PermGuard>
      ),
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<KeyOutlined />}
        title="访问令牌"
        subtitle={['机器客户端调用内容 API 的长期凭据（PAT）：可吊销、可过期、作用域受限']}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <PermGuard perm="api_token:manage">
              <Button type="primary" onClick={() => setCreateOpen(true)}>
                签发令牌
              </Button>
            </PermGuard>
          </Space>
        }
      />

      <Alert
        type="info"
        showIcon
        title="明文令牌只在签发时显示一次"
        description="列表里只有前缀。签发后请立即把明文保存到客户端配置 —— 关闭弹窗后无法再次查看，只能吊销重建。"
        style={{ marginBottom: 16 }}
      />

      <DataTable<ApiTokenItem>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={{ page: 1, pageSize: 200, total: items.length, onPageChange: () => undefined }}
        empty={{
          hasFilter: false,
          filteredText: '没有符合条件的令牌',
          emptyText: '还没有访问令牌。点右上角「签发令牌」为机器客户端创建第一把钥匙。',
        }}
      />

      <ModalForm
        open={createOpen}
        title="签发访问令牌"
        onCancel={() => setCreateOpen(false)}
        onSubmit={handleCreate}
        okText="签发"
        initialValues={{ scopes: ['content:read'], expires_days: 90 }}
        formStyle={{ marginTop: 16 }}
      >
        <Form.Item
          name="name"
          label="用途备注"
          rules={[{ required: true, message: '请填写用途（如「渲染站渲染器」）' }, { max: 100 }]}
        >
          <Input placeholder="给谁用 / 用在哪" />
        </Form.Item>
        <Form.Item name="scopes" label="作用域" initialValue={['content:read']}>
          <Select mode="multiple" options={SCOPE_OPTIONS} placeholder="默认 content:read" />
        </Form.Item>
        <Form.Item name="expires_days" label="有效期">
          <Select options={EXPIRE_OPTIONS} />
        </Form.Item>
      </ModalForm>

      {/* 明文令牌一次性弹窗：只有这一个时刻能看到全文 */}
      <Modal
        open={created !== null}
        title="令牌已签发 —— 请立即保存"
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
            <Alert
              type="warning"
              showIcon
              title="关闭本弹窗后无法再次查看明文"
              description="这是唯一一次展示。请复制保存到客户端配置；怀疑泄露时回到本页吊销并重新签发。"
            />
            <Typography.Paragraph code copyable style={{ marginBottom: 0, wordBreak: 'break-all' }}>
              {created.token}
            </Typography.Paragraph>
            <Typography.Text type="secondary">
              用法：请求头 <code>Authorization: Bearer &lt;令牌&gt;</code>，仅限内容 API（/api/v1/content/*）。
            </Typography.Text>
          </Space>
        )}
      </Modal>
    </div>
  )
}
