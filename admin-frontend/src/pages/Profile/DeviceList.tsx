import { useCallback, useEffect, useState } from 'react'
import { Button, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { apiDelete, apiGet } from '../../api/client'
import { formatDateTime } from '../../utils/datetime'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'

/**
 * 登录设备列表：哪台设备在什么时候登录过、最近什么时候活跃，可以逐台下线。
 *
 * 为什么值得单独一块：密码泄露时，用户第一个问题是「现在有谁在登着我的号」。
 * 只有「一键全踢」的话，正常在用的设备也会被踢掉，人反而不敢用这个按钮。
 *
 * 两条 UI 约定：
 * - **本机必须标出来**：否则用户会把「下线」当成「换个地方登录」，踢掉自己正在用的这台；
 * - 时间用相对描述 + 绝对时间并存（`formatDateTime`），排查时看绝对值，日常看相对值。
 */

interface SessionItem {
  sid: string
  device: string
  ip: string
  created_at: string
  last_seen: string
  current: boolean
}

interface SessionListData {
  items: SessionItem[]
  total: number
}

export default function DeviceList() {
  const [rows, setRows] = useState<SessionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<SessionListData>('/admin/sessions')
      setRows(data.items ?? [])
    } catch (err) {
      toast.error(getErrorMessage(err, '登录设备加载失败'))
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const revokeOne = async (sid: string) => {
    setBusy(true)
    try {
      await apiDelete(`/admin/sessions/${sid}`)
      toast.success('已下线该设备')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '下线失败'))
    } finally {
      setBusy(false)
    }
  }

  const revokeOthers = async () => {
    setBusy(true)
    try {
      await apiDelete<{ revoked: number }>('/admin/sessions')
      toast.success('已退出其他设备')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setBusy(false)
    }
  }

  const columns: ColumnsType<SessionItem> = [
    {
      title: '设备',
      dataIndex: 'device',
      width: 220,
      render: (device: string, row) => (
        <Space size={6} wrap={false}>
          <span style={{ whiteSpace: 'nowrap' }}>{device}</span>
          {row.current ? <Tag color="blue">本机</Tag> : null}
        </Space>
      ),
    },
    { title: 'IP', dataIndex: 'ip', width: 140, render: (ip: string) => ip || '—' },
    {
      title: '登录时间',
      dataIndex: 'created_at',
      width: 180,
      render: (value: string) => (value ? formatDateTime(value) : '—'),
    },
    {
      title: '最近活跃',
      dataIndex: 'last_seen',
      width: 180,
      render: (value: string) => (value ? formatDateTime(value) : '—'),
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, row) => (
        <Popconfirm
          title={row.current ? '下线当前设备？' : '下线该设备？'}
          description={row.current ? '这等价于退出登录，你需要重新登录。' : '该设备会立即失去访问权。'}
          okText="下线"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => revokeOne(row.sid)}
        >
          <Button type="link" size="small" danger loading={busy}>
            下线
          </Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button onClick={load} loading={loading}>
          刷新
        </Button>
        <Popconfirm
          title="退出其他设备？"
          description="除本机外的所有设备会立即失去访问权。"
          okText="确认"
          cancelText="取消"
          onConfirm={revokeOthers}
        >
          <Button disabled={rows.filter((row) => !row.current).length === 0} loading={busy}>
            退出其他设备
          </Button>
        </Popconfirm>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          共 {rows.length} 台设备；下线后该设备需要重新登录
        </Typography.Text>
      </Space>
      <Table<SessionItem>
        rowKey="sid"
        size="small"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={false}
        locale={{ emptyText: '暂无其他登录设备' }}
      />
    </div>
  )
}
