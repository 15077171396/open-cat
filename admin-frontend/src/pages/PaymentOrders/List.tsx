import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import { EyeOutlined, ReloadOutlined, WalletOutlined } from '@ant-design/icons'
import { apiGet } from '../../api/client'
import ListPageHeader from '../../components/ListPageHeader'
import { TableEmpty } from '../../components/TableEmpty'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import { formatTime } from '../../utils/datetime'

interface PaymentOrder {
  order_no: string
  user_id: number
  user_account: string | null
  product_name: string
  product_kind: string
  amount: string
  channel: string
  status: string
  provider_trade_no: string | null
  paid_at: string | null
  created_at: string | null
  expires_at: string | null
  provider_payload?: Record<string, unknown> | null
}

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '待支付', color: 'orange' },
  paid: { label: '已支付', color: 'green' },
  closed: { label: '已关闭', color: 'default' },
  refunded: { label: '已退款', color: 'purple' },
  failed: { label: '失败', color: 'red' },
}

const CHANNEL_LABEL: Record<string, string> = {
  mock: '模拟支付',
  wechat: '微信支付',
  alipay: '支付宝',
}

export default function PaymentOrdersList() {
  const [loading, setLoading] = useState(true)
  const [rows, setRows] = useState<PaymentOrder[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [status, setStatus] = useState('')
  const [channel, setChannel] = useState('')
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [detail, setDetail] = useState<PaymentOrder | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string | number> = { page, page_size: pageSize }
      if (status) params.status = status
      if (channel) params.channel = channel
      if (keyword) params.keyword = keyword
      const data = await apiGet<{ items: PaymentOrder[]; total: number }>('/admin/payment-orders', {
        params,
      })
      setRows(data.items || [])
      setTotal(data.total || 0)
    } catch (err) {
      toast.error(getErrorMessage(err, '加载订单失败'))
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, status, channel, keyword])

  useEffect(() => {
    void load()
  }, [load])

  const handleSearch = (value: string) => {
    setKeyword(value.trim())
    setPage(1)
  }

  const columns: TableProps<PaymentOrder>['columns'] = [
    {
      title: '订单号',
      dataIndex: 'order_no',
      key: 'order_no',
      width: 210,
      render: (value: string) => (
        <Typography.Text copyable style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: '用户',
      dataIndex: 'user_account',
      key: 'user_account',
      render: (value: string | null, row) => value || `#${row.user_id}`,
    },
    {
      title: '套餐',
      key: 'product',
      render: (_v, row) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text>{row.product_name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.product_kind === 'vip' ? 'VIP' : '积分'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '金额',
      dataIndex: 'amount',
      key: 'amount',
      width: 100,
      render: (value: string) => <Typography.Text strong>¥{value}</Typography.Text>,
    },
    {
      title: '渠道',
      dataIndex: 'channel',
      key: 'channel',
      width: 100,
      render: (value: string) => CHANNEL_LABEL[value] || value,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (value: string) => {
        const meta = STATUS_META[value] || { label: value, color: 'default' }
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '下单时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (value: string | null) => (value ? formatTime(value) : '—'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_v, row) => (
        <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => setDetail(row)}>
          详情
        </Button>
      ),
    },
  ]

  const hasFilter = !!status || !!channel || !!keyword

  return (
    <div>
      <ListPageHeader
        icon={<WalletOutlined />}
        title="支付订单"
        extra={
          <Space>
            <Input.Search
              placeholder="订单号 / 用户账号"
              allowClear
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onSearch={handleSearch}
              style={{ width: 220 }}
            />
            <Select
              value={status}
              onChange={(value) => {
                setStatus(value)
                setPage(1)
              }}
              style={{ width: 120 }}
              options={[
                { value: '', label: '全部状态' },
                ...Object.entries(STATUS_META).map(([value, meta]) => ({ value, label: meta.label })),
              ]}
            />
            <Select
              value={channel}
              onChange={(value) => {
                setChannel(value)
                setPage(1)
              }}
              style={{ width: 130 }}
              options={[
                { value: '', label: '全部渠道' },
                { value: 'mock', label: '模拟支付' },
                { value: 'wechat', label: '微信支付' },
                { value: 'alipay', label: '支付宝' },
              ]}
            />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
              刷新
            </Button>
          </Space>
        }
      />

      <Card variant="outlined" styles={{ body: { padding: 0 } }}>
        <Table<PaymentOrder>
          rowKey="order_no"
          columns={columns}
          dataSource={rows}
          loading={loading}
          scroll={{ x: 1100 }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 笔`,
            onChange: (nextPage, nextSize) => {
              setPage(nextPage)
              setPageSize(nextSize)
            },
          }}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText="没有符合筛选条件的订单"
                emptyText="还没有支付订单"
                onReset={() => {
                  setStatus('')
                  setChannel('')
                  setKeyword('')
                  setKeywordInput('')
                  setPage(1)
                }}
              />
            ),
          }}
        />
      </Card>

      <Drawer
        open={!!detail}
        onClose={() => setDetail(null)}
        title="订单详情"
        // antd 6 已弃用 Drawer 的 width（控制台会打 `width` is deprecated，请改用 `size`）；
        // size 直接收数字像素值，所以 520 就是原来的 520px 宽
        size={520}
        destroyOnHidden
      >
        {detail ? (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="订单号">{detail.order_no}</Descriptions.Item>
            <Descriptions.Item label="用户">{detail.user_account || `#${detail.user_id}`}</Descriptions.Item>
            <Descriptions.Item label="套餐">
              {detail.product_name}（{detail.product_kind === 'vip' ? 'VIP' : '积分'}）
            </Descriptions.Item>
            <Descriptions.Item label="金额">¥{detail.amount}</Descriptions.Item>
            <Descriptions.Item label="渠道">{CHANNEL_LABEL[detail.channel] || detail.channel}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={(STATUS_META[detail.status] || {}).color || 'default'}>
                {(STATUS_META[detail.status] || {}).label || detail.status}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="第三方交易号">{detail.provider_trade_no || '—'}</Descriptions.Item>
            <Descriptions.Item label="下单时间">{detail.created_at ? formatTime(detail.created_at) : '—'}</Descriptions.Item>
            <Descriptions.Item label="支付时间">{detail.paid_at ? formatTime(detail.paid_at) : '—'}</Descriptions.Item>
            <Descriptions.Item label="过期时间">{detail.expires_at ? formatTime(detail.expires_at) : '—'}</Descriptions.Item>
            <Descriptions.Item label="渠道摘要">
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {JSON.stringify(detail.provider_payload || {}, null, 2)}
              </pre>
            </Descriptions.Item>
          </Descriptions>
        ) : null}
      </Drawer>
    </div>
  )
}
