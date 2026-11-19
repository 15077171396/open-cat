import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Space, Switch, Tag, Typography } from 'antd'
import { FileSearchOutlined, ReloadOutlined } from '@ant-design/icons'
import type { TableProps } from 'antd'
import dayjs from 'dayjs'

import { apiGet } from '../../api/client'
import { toast } from '../../utils/toast'
import { getErrorMessage } from '../../utils/error'
import { DataTable, PageHeader } from '../../components/ui'

/**
 * 系统错误日志（/monitor/errors）：最近 ERROR+ 事件的环形缓冲（Redis，最多 500 条）。
 *
 * 定位：排错第一入口 —— 不用登服务器翻日志文件就能看到「刚发生了什么错」；
 * 每条带 request_id，可与应用日志/审计日志串成同一条请求线索。
 * 观测数据不落库：只展示缓冲里的最近事件，刷新即最新（支持自动刷新）。
 */

interface ErrorLogItem {
  ts: string | null
  level: string
  message: string
  name: string
  function: string
  line: number | null
  request_id: string | null
}

interface ErrorLogsData {
  available: boolean
  error: string | null
  items: ErrorLogItem[]
}

const REFRESH_INTERVAL_MS = 15_000

const LEVEL_COLOR: Record<string, string> = {
  ERROR: 'red',
  CRITICAL: 'volcano',
}

/** 系统错误日志页（只读观测，复用 dashboard:view）。 */
export default function MonitorErrors() {
  const [data, setData] = useState<ErrorLogsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const timerRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await apiGet<ErrorLogsData>('/admin/monitor/errors', { params: { limit: 200 } }))
    } catch (error) {
      toast.error(getErrorMessage(error, '错误日志加载失败'))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!autoRefresh) {
      if (timerRef.current) window.clearInterval(timerRef.current)
      timerRef.current = null
      return
    }
    timerRef.current = window.setInterval(() => void load(), REFRESH_INTERVAL_MS)
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [autoRefresh, load])

  const columns: TableProps<ErrorLogItem>['columns'] = [
    {
      title: '级别',
      dataIndex: 'level',
      width: 100,
      render: (value: string) => <Tag color={LEVEL_COLOR[value] ?? 'red'}>{value}</Tag>,
    },
    {
      title: '时间',
      dataIndex: 'ts',
      width: 190,
      render: (value: string | null) => (value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss.SSS') : '—'),
    },
    {
      title: '位置',
      key: 'location',
      width: 260,
      ellipsis: true,
      render: (_: unknown, record: ErrorLogItem) => (
        <Typography.Text type="secondary">
          {record.name}
          {record.function ? `:${record.function}` : ''}
          {record.line ? `:${record.line}` : ''}
        </Typography.Text>
      ),
    },
    {
      title: '请求 ID',
      dataIndex: 'request_id',
      width: 120,
      render: (value: string | null) =>
        value && value !== '-' ? (
          <Typography.Text copyable={{ text: value, tooltips: ['复制', '已复制'] }}>复制</Typography.Text>
        ) : (
          '—'
        ),
    },
    {
      title: '错误信息',
      dataIndex: 'message',
      ellipsis: true,
      render: (value: string) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{value}</Typography.Text>
      ),
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<FileSearchOutlined />}
        title="错误日志"
        subtitle={['最近 ERROR 及以上事件的环形缓冲（Redis，最多保留 500 条）']}
        count={{ total: data?.items.length ?? 0, unit: '条' }}
        actions={
          <Space>
            <span>自动刷新</span>
            <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
              刷新
            </Button>
          </Space>
        }
      />

      {data && !data.available && (
        <Alert
          type="info"
          showIcon
          title={data.error || '暂无错误事件'}
          description="缓冲里没有可展示的错误事件；若持续显示此提示且系统行为异常，请检查 Redis 服务。"
          style={{ marginBottom: 12 }}
        />
      )}
      {data?.available && data.items.length === 0 && (
        <Alert
          type="success"
          showIcon
          title="暂无错误事件"
          description="系统最近没有发生 ERROR 级别以上的问题。"
          style={{ marginBottom: 12 }}
        />
      )}

      <DataTable<ErrorLogItem>
        columns={columns}
        dataSource={data?.items ?? []}
        loading={loading}
        scrollX={1000}
        pagination={undefined}
        empty={{
          hasFilter: false,
          filteredText: '没有可展示的错误事件',
          emptyText: '暂无错误事件',
        }}
      />
    </div>
  )
}
