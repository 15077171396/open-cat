import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Space, Switch, Tag, Typography } from 'antd'
import { ApiOutlined, ReloadOutlined } from '@ant-design/icons'
import type { TableProps } from 'antd'

import { apiGet } from '../../api/client'
import { toast } from '../../utils/toast'
import { getErrorMessage } from '../../utils/error'
import { DataTable, PageHeader } from '../../components/ui'

/**
 * 接口监控（/monitor/api）：调用量 / 错误率 Top + 慢接口明细。
 *
 * 数据来自 Redis 小时桶聚合（当前 + 上一小时）与慢请求环形明细，零 DB 写入；
 * 统计开关关闭或 Redis 不可用时显示「尚未采集」，不 500。
 */

interface ApiStatsEntry {
  key: string
  calls: number
  '4xx': number
  '5xx': number
  error_rate: number | null
}

interface SlowRequest {
  ts: string | null
  method: string
  path: string
  status: number
  duration_ms: number
  user_id: string | null
}

interface ApiStatsData {
  available: boolean
  enabled: boolean
  slow_threshold_ms: number
  min_samples: number
  unique_paths: number
  total_calls: number
  top_calls: ApiStatsEntry[]
  top_errors: ApiStatsEntry[]
  slow_requests: SlowRequest[]
}

const REFRESH_INTERVAL_MS = 20_000

const STATUS_COLOR: Record<string, string> = {
  '2xx': 'green',
  '4xx': 'orange',
  '5xx': 'red',
  other: 'default',
}

/** 接口监控页（只读运维，复用 dashboard:view）。 */
export default function MonitorApiStats() {
  const [data, setData] = useState<ApiStatsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const timerRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await apiGet<ApiStatsData>('/admin/monitor/api-stats', { params: { limit: 20 } }))
    } catch (error) {
      toast.error(getErrorMessage(error, '接口统计加载失败'))
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

  const entryColumns: TableProps<ApiStatsEntry>['columns'] = [
    { title: '接口', dataIndex: 'key', ellipsis: true, render: (v: string) => <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</Typography.Text> },
    { title: '调用次数', dataIndex: 'calls', width: 110, align: 'right' },
    { title: '4xx', dataIndex: '4xx', width: 90, align: 'right' },
    {
      title: '5xx',
      dataIndex: '5xx',
      width: 90,
      align: 'right',
      render: (v: number) => (v > 0 ? <Tag color="red">{v}</Tag> : v),
    },
    {
      title: '错误率',
      dataIndex: 'error_rate',
      width: 110,
      align: 'right',
      render: (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`),
    },
  ]

  const slowColumns: TableProps<SlowRequest>['columns'] = [
    {
      title: '时间',
      dataIndex: 'ts',
      width: 180,
      render: (v: string | null) => (v ? v.replace('T', ' ').slice(0, 19) : '—'),
    },
    { title: '方法', dataIndex: 'method', width: 80 },
    {
      title: '路径',
      dataIndex: 'path',
      ellipsis: true,
      render: (v: string) => <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</Typography.Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: number) => {
        const bucket = v >= 500 ? '5xx' : v >= 400 ? '4xx' : v >= 200 ? '2xx' : 'other'
        return <Tag color={STATUS_COLOR[bucket]}>{v}</Tag>
      },
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      width: 110,
      align: 'right',
      render: (v: number) => (
        <Typography.Text type={v >= 5000 ? 'danger' : undefined}>
          {v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`}
        </Typography.Text>
      ),
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<ApiOutlined />}
        title="接口监控"
        subtitle={['调用量 / 错误率 Top（当前小时 + 上一小时）与慢接口明细，聚合数据存于 Redis']}
        count={{ total: data?.total_calls ?? 0, unit: '次' }}
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
          title="尚未采集"
          description="统计开关已关闭或数据不可读；可在「系统设置 → 访问限流」开启接口调用统计。"
          style={{ marginBottom: 12 }}
        />
      )}

      <div className="dash-hero" style={{ margin: '4px 0 12px', padding: 16 }}>
        <div className="dash-hero__main">
          <div className="dash-hero__stat">
            <span>独立路径</span>
            <strong>{data?.unique_paths ?? '—'}</strong>
          </div>
          <div className="dash-hero__stat">
            <span>总调用（两小时）</span>
            <strong>{data?.total_calls ?? '—'}</strong>
          </div>
          <div className="dash-hero__stat">
            <span>慢接口阈值</span>
            <strong>{data ? `${data.slow_threshold_ms}ms` : '—'}</strong>
          </div>
        </div>
      </div>

      <DataTable<ApiStatsEntry>
        columns={entryColumns}
        dataSource={data?.top_calls ?? []}
        loading={loading}
        rowKey="key"
        scrollX={620}
        pagination={undefined}
        empty={{
          hasFilter: false,
          filteredText: '当前统计窗口内没有足够的调用样本',
          emptyText: '暂无接口调用数据（样本量 < 5 次的路径不参与排序）',
        }}
      />

      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 16, marginBottom: 8 }}>
        慢接口明细（耗时 ≥ {data?.slow_threshold_ms ?? 1000}ms 的请求，最多保留 200 条）
      </Typography.Paragraph>
      <DataTable<SlowRequest>
        columns={slowColumns}
        dataSource={data?.slow_requests ?? []}
        loading={loading}
        rowKey={(record) => `${record.ts}-${record.path}-${record.duration_ms}`}
        scrollX={760}
        pagination={undefined}
        empty={{
          hasFilter: false,
          filteredText: '窗口内没有慢请求',
          emptyText: '暂无慢请求（这通常是好消息）',
        }}
      />
    </div>
  )
}
