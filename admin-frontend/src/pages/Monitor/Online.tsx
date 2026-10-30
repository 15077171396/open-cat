import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Input, Popconfirm, Space, Tag } from 'antd'
import type { TableProps } from 'antd'
import { LogoutOutlined, ReloadOutlined, WifiOutlined } from '@ant-design/icons'
import { apiGet, apiPost } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import { formatDateTime } from '../../utils/datetime'
import { toast } from '../../utils/toast'
import { getErrorMessage } from '../../utils/error'
import { PageHeader, ListToolbar, DataTable } from '../../components/ui'

interface OnlineSession {
  sid: string
  uid: number
  account: string
  nickname: string | null
  role: string
  device: string
  ip: string
  created_at: number
  last_seen: number
}

interface OnlineData {
  available: boolean
  items: OnlineSession[]
  total: number
  unique_users: number
  page: number
  page_size: number
  scanned: number
}

const PAGE_SIZE = 20

function timeOf(seconds: number): string {
  if (!seconds) return '—'
  return formatDateTime(new Date(seconds * 1000).toISOString())
}

/** 全站登录会话（只读列表 + 复用用户强制下线接口）。 */
export default function MonitorOnline() {
  const { isSuper, permissions } = useAuthStore()
  const canKick = isSuper || (permissions?.includes('user:edit') ?? false)
  const [data, setData] = useState<OnlineData | null>(null)
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [page, setPage] = useState(1)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await apiGet<OnlineData>('/admin/monitor/online', {
        params: { keyword: appliedKeyword, page, page_size: PAGE_SIZE },
      })
      setData(result)
    } catch (error) {
      toast.error(getErrorMessage(error, '在线用户加载失败'))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [appliedKeyword, page])

  useEffect(() => {
    void load()
  }, [load])

  const kick = async (row: OnlineSession) => {
    try {
      await apiPost(`/admin/users/${row.uid}/force-logout`)
      toast.success(`已强制下线 ${row.account || row.uid}`)
      await load()
    } catch (error) {
      toast.error(getErrorMessage(error, '强制下线失败'))
    }
  }

  const columns: TableProps<OnlineSession>['columns'] = [
    {
      title: '用户',
      key: 'account',
      render: (_, row) => (
        <Space size={6}>
          <span>{row.nickname || row.account || `#${row.uid}`}</span>
          <Tag bordered={false}>{row.role || 'user'}</Tag>
        </Space>
      ),
    },
    { title: '账号', dataIndex: 'account', key: 'account' },
    { title: '设备', dataIndex: 'device', key: 'device' },
    {
      title: 'IP',
      dataIndex: 'ip',
      key: 'ip',
      render: (ip: string) => <code>{ip || '—'}</code>,
    },
    { title: '登录时间', dataIndex: 'created_at', key: 'created_at', render: (value: number) => timeOf(value) },
    { title: '最近活跃', dataIndex: 'last_seen', key: 'last_seen', render: (value: number) => timeOf(value) },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_, row) =>
        canKick ? (
          <Popconfirm
            title={`强制下线 ${row.account || row.uid}？`}
            description="该用户所有设备的登录会话都会失效。"
            onConfirm={() => void kick(row)}
          >
            <Button size="small" danger type="link" icon={<LogoutOutlined />}>
              强制下线
            </Button>
          </Popconfirm>
        ) : null,
    },
  ]

  return (
    <div>
      <PageHeader
        icon={<WifiOutlined />}
        title="在线用户"
        subtitle={
          data?.available
            ? `当前 ${data.total} 个登录会话 / ${data.unique_users} 个用户`
            : '全站登录会话（Redis 会话簿）'
        }
        actions={
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        }
      />

      {data && !data.available && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title="Redis 不可用，暂时读不到在线会话"
          description="请检查 Redis 服务与连接配置；会话簿本身是 fail-open 的，不影响已登录用户继续访问。"
        />
      )}

      <ListToolbar
        hasFilter={appliedKeyword.length > 0}
        onReset={() => {
          setKeyword('')
          setAppliedKeyword('')
          setPage(1)
        }}
        extra={
          <span style={{ color: 'var(--app-text-3)', fontSize: 12 }}>
            {data?.available ? `扫描 ${data.scanned} 个会话键` : '—'}
          </span>
        }
      >
        <Input.Search
          allowClear
          aria-label="搜索在线用户"
          placeholder="搜索账号 / 昵称 / 设备 / IP"
          style={{ width: 280 }}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          onSearch={(value) => {
            setPage(1)
            setAppliedKeyword(value.trim())
          }}
        />
      </ListToolbar>

      <DataTable<OnlineSession>
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey="sid"
        loading={loading}
        scrollX={980}
        pagination={
          data
            ? {
                page,
                pageSize: PAGE_SIZE,
                total: data.total,
                onPageChange: setPage,
              }
            : undefined
        }
        empty={{
          hasFilter: appliedKeyword.length > 0,
          filteredText: '没有匹配的在线用户',
          emptyText: data?.available ? '当前没有在线会话' : 'Redis 不可用',
          onReset: () => {
            setKeyword('')
            setAppliedKeyword('')
          },
        }}
      />
    </div>
  )
}
