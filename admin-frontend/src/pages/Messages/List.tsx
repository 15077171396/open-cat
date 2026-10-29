import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import { NotificationOutlined, ReloadOutlined, SendOutlined } from '@ant-design/icons'
import { apiGet, apiPost } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { formatTime } from '../../utils/datetime'
import { toast } from '../../utils/toast'
import type { PaginatedResponse, AdminUser } from '../../types'
import PageHeader from '../../components/ui/PageHeader'
import { DataTable } from '../../components/ui'

/**
 * 站内消息通知：给用户发系统通知（全站 / 指定用户）。
 *
 * 这是消息中心「系统通知」tab 的人工数据源（另一个是内容下架时的自动通知）。
 * 发送权限单独成码（`message:send`）：群发是**面向全体用户**的触达能力，
 * 发错一次就是全站可见事故，与「看已发记录」不是同一类授权。
 */

interface MessageBatch {
  batch_id: string
  title: string
  content: string
  link?: string | null
  sent_count: number
  read_count: number
  created_at: string | null
}

interface SendFormValues {
  title: string
  content: string
  link?: string
  target: 'all' | 'users'
  user_ids?: number[]
}

const PAGE_SIZE = 20

export default function MessageList() {
  const [items, setItems] = useState<MessageBatch[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [sendOpen, setSendOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const [userOptions, setUserOptions] = useState<{ label: string; value: number }[]>([])
  const [form] = Form.useForm<SendFormValues>()
  const target = Form.useWatch('target', form)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<PaginatedResponse<MessageBatch>>('/admin/messages', {
        params: { page, page_size: PAGE_SIZE },
      })
      setItems(data.items || [])
      setTotal(data.total || 0)
    } catch (err) {
      toast.error(getErrorMessage(err, '加载通知记录失败'))
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    void load()
  }, [load])

  /** 指定用户时的远程搜索：按账号/昵称模糊查，取前 20 条足够选人 */
  const searchUsers = async (keyword: string) => {
    const kw = keyword.trim()
    if (!kw) return
    try {
      const data = await apiGet<PaginatedResponse<AdminUser>>('/admin/users', {
        params: { keyword: kw, page: 1, page_size: 20 },
      })
      setUserOptions(
        (data.items || []).map((u) => ({ value: u.id, label: `${u.account}（#${u.id}）` })),
      )
    } catch {
      // 搜索失败不弹错：用户还能继续输入，不至于把整个发送流程卡死
    }
  }

  const handleSend = async () => {
    try {
      const values = await form.validateFields()
      setSending(true)
      const result = await apiPost<{ batch_id: string; sent_count: number }>('/admin/messages', {
        title: values.title.trim(),
        content: values.content.trim(),
        link: values.link?.trim() || null,
        target: values.target,
        user_ids: values.target === 'users' ? values.user_ids : null,
      })
      toast.success(`已发送给 ${result.sent_count} 位用户`)
      setSendOpen(false)
      form.resetFields()
      setPage(1)
      await load()
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '发送失败，请稍后重试'))
    } finally {
      setSending(false)
    }
  }

  const columns: TableProps<MessageBatch>['columns'] = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      width: 220,
      render: (v: string) => <Typography.Text strong>{v}</Typography.Text>,
    },
    {
      title: '内容',
      dataIndex: 'content',
      key: 'content',
      render: (v: string, row) => (
        <div>
          <Typography.Paragraph
            ellipsis={{ tooltip: v, rows: 2 }}
            style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
          >
            {v}
          </Typography.Paragraph>
          {row.link ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              跳转：{row.link}
            </Typography.Text>
          ) : null}
        </div>
      ),
    },
    {
      title: '送达 / 已读',
      key: 'stats',
      width: 140,
      render: (_: unknown, row) => (
        <Space size={4}>
          <Tag color="blue" style={{ margin: 0 }}>
            {row.sent_count} 人
          </Tag>
          <Tag color={row.read_count > 0 ? 'green' : 'default'} style={{ margin: 0 }}>
            读 {row.read_count}
          </Tag>
        </Space>
      ),
    },
    {
      title: '发送时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v: string | null) => formatTime(v),
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<NotificationOutlined />}
        title="消息通知"
        subtitle="向用户发送站内系统通知，会出现在「个人中心 → 我的消息 → 系统通知」"
        count={{ total: total, unit: "批次" }}
        actions={
          <>
            <Tooltip title="重新拉取发送记录">
              <Button icon={<ReloadOutlined />} onClick={() => void load()}>
                刷新
              </Button>
            </Tooltip>
            <PermGuard perm="message:send">
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={() => {
                  form.resetFields()
                  setSendOpen(true)
                }}
              >
                发送通知
              </Button>
            </PermGuard>

          </>
        }
      />

      <DataTable<MessageBatch>
        dataSource={items}
        columns={columns}
        rowKey="batch_id"
        loading={loading}
        scrollX={900}
        pagination={{ page: page, pageSize: PAGE_SIZE, total: total, onPageChange: setPage }}
        empty={{ hasFilter: false, filteredText: "没有符合条件的通知", emptyText: "还没有发过系统通知", onReset: () => setPage(1) }}
      />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：一次群发会给每个接收用户各写一条站内消息，上表按批次聚合展示送达与已读情况；
        发送操作会写入操作审计。
      </Typography.Text>

      <Modal
        title="发送系统通知"
        open={sendOpen}
        onOk={handleSend}
        onCancel={() => setSendOpen(false)}
        okText="发送"
        cancelText="取消"
        confirmLoading={sending}
        width={560}
        destroyOnHidden={false}
      >
        <Form form={form} layout="vertical" initialValues={{ target: 'all' }}>
          <Form.Item
            name="title"
            label="通知标题"
            rules={[
              { required: true, message: '请填写通知标题' },
              { max: 60, message: '标题最长 60 字' },
            ]}
          >
            <Input placeholder="例如：平台新增「AI 绘画」专题教程" maxLength={60} showCount />
          </Form.Item>

          <Form.Item
            name="content"
            label="通知内容"
            rules={[
              { required: true, message: '请填写通知内容' },
              { max: 1000, message: '内容最长 1000 字' },
            ]}
            extra="纯文本展示，不支持 HTML / Markdown"
          >
            <Input.TextArea
              placeholder="写清「发生了什么、和用户有什么关系、下一步做什么」"
              autoSize={{ minRows: 4, maxRows: 10 }}
              maxLength={1000}
              showCount
            />
          </Form.Item>

          <Form.Item
            name="link"
            label="跳转链接（选填）"
            rules={[
              {
                validator: (_, value: string) => {
                  const v = (value || '').trim()
                  if (!v) return Promise.resolve()
                  if (!v.startsWith('/') || v.startsWith('//')) {
                    return Promise.reject(new Error('请填写以 / 开头的站内路径，例如 /tutorials/12'))
                  }
                  return Promise.resolve()
                },
              },
            ]}
            extra="用户点击通知后跳转的页面，必须是站内路径"
          >
            <Input placeholder="/tutorials/12" maxLength={500} />
          </Form.Item>

          <Form.Item name="target" label="接收范围">
            <Radio.Group>
              <Radio value="all">全站用户</Radio>
              <Radio value="users">指定用户</Radio>
            </Radio.Group>
          </Form.Item>

          {target === 'users' ? (
            <Form.Item
              name="user_ids"
              label="选择用户"
              rules={[{ required: true, message: '请选择至少一个用户' }]}
              extra="输入账号搜索，最多可选 500 人；发给更多人请用「全站用户」"
            >
              <Select
                mode="multiple"
                showSearch
                filterOption={false}
                onSearch={searchUsers}
                options={userOptions}
                placeholder="输入用户账号搜索"
                notFoundContent="输入账号后搜索"
                maxTagCount={6}
              />
            </Form.Item>
          ) : null}
        </Form>
      </Modal>
    </div>
  )
}
