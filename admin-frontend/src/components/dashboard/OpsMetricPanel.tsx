import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Segmented, Space } from 'antd'
import { ArrowDownOutlined, ArrowRightOutlined, ArrowUpOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ReactNode } from 'react'

import { apiGet } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import ExportButton from '../ui/ExportButton'
import TrendChart from './TrendChart'
import type { DashboardTrendPoint } from '../../types'

/**
 * 系统指标趋势（看板卡片）：多指标概览卡（当前读数 + 环比）+ 单指标趋势回看。
 *
 * 数据来自 ops_metric_snapshots（每小时一帧，由 scheduler 的 ops_metric_snapshot
 * 任务写入）：概览卡读 `/ops-metrics/summary`（最近两帧对比），趋势读
 * `/ops-metrics`（单指标窗口回看）。独立拉取、独立降级：快照任务还没跑 /
 * 迁移未执行 / 接口 404 都只让本卡片显示提示，不拖垮看板其他卡片。
 */

interface MetricOption {
  value: string
  label: string
}

interface OpsMetricsData {
  metric: string
  label: string
  hours: number
  available: boolean
  points: { taken_at: string | null; count: number }[]
  metric_options: MetricOption[]
}

interface SummaryItem {
  key: string
  label: string
  good_when_up: boolean
  value: number | null
  previous: number | null
  delta: number | null
}

interface SummaryData {
  available: boolean
  frame_count: number
  taken_at: string | null
  items: SummaryItem[]
}

/** 可切换的回看窗口（小时） */
const WINDOW_OPTIONS = [
  { label: '近 24 小时', value: 24 },
  { label: '近 3 天', value: 72 },
  { label: '近 7 天', value: 168 },
]

const cardTitleStyle = { fontSize: 15, fontWeight: 600 } as const

/** 环比箭头与分色：按指标语义方向（good_when_up）决定升/降是好是坏 */
function deltaTone(item: SummaryItem): ReactNode {
  const { delta, good_when_up } = item
  if (delta === null || delta === undefined || delta === 0) {
    return (
      <span style={{ color: 'var(--app-text-3)', fontSize: 12 }}>
        <ArrowRightOutlined /> 持平
      </span>
    )
  }
  const up = delta > 0
  const good = up === good_when_up
  const color = good ? 'var(--app-success)' : 'var(--app-danger)'
  const Arrow = up ? ArrowUpOutlined : ArrowDownOutlined
  return (
    <span style={{ color, fontSize: 12 }}>
      <Arrow /> {up ? '+' : ''}
      {delta}
    </span>
  )
}

export default function OpsMetricPanel() {
  const [data, setData] = useState<OpsMetricsData | null>(null)
  const [summary, setSummary] = useState<SummaryData | null>(null)
  const [loading, setLoading] = useState(true)
  const [metric, setMetric] = useState<string>('users_total')
  const [hours, setHours] = useState<number>(72)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // 概览与趋势两个请求并发：任一失败只降级自己的区块
      const [trendResult, summaryResult] = await Promise.allSettled([
        apiGet<OpsMetricsData>('/admin/ops-metrics', { params: { metric, hours } }),
        apiGet<SummaryData>('/admin/ops-metrics/summary'),
      ])
      if (trendResult.status === 'fulfilled') {
        setData(trendResult.value)
      } else {
        setData(null)
        toast.error(getErrorMessage(trendResult.reason, '指标趋势加载失败'))
      }
      setSummary(summaryResult.status === 'fulfilled' ? summaryResult.value : null)
    } finally {
      setLoading(false)
    }
  }, [metric, hours])

  useEffect(() => {
    void load()
  }, [load])

  // 接口下发 metric_options（与后端 METRIC_LABELS 同源）；首次拿到后再切指标
  const options = useMemo(() => data?.metric_options ?? [{ value: 'users_total', label: '注册用户总量' }], [data])
  const currentLabel = useMemo(
    () => data?.label ?? options.find((o) => o.value === metric)?.label ?? metric,
    [data, options, metric],
  )

  // 快照点 → TrendChart 数据形状（taken_at → date）
  const points = useMemo<DashboardTrendPoint[]>(
    () =>
      (data?.points ?? []).map((p) => ({
        count: p.count,
        date: p.taken_at ? p.taken_at.slice(5, 16).replace('T', ' ') : '',
      })),
    [data],
  )
  const latest = points.length > 0 ? points[points.length - 1].count : null

  const windowLabel = WINDOW_OPTIONS.find((w) => w.value === hours)?.label ?? `近 ${hours} 小时`

  return (
    <Card
      className="dash-card"
      variant="outlined"
      title={<span style={cardTitleStyle}>系统指标趋势</span>}
      extra={
        <Segmented
          size="small"
          value={hours}
          onChange={(value) => setHours(value as number)}
          options={WINDOW_OPTIONS.map((w) => ({ label: w.label, value: w.value }))}
        />
      }
    >
      {data && !data.available && (
        <Alert
          type="info"
          showIcon
          title="尚未采集"
          description="快照任务（每小时）还没有产出数据；新部署需等待任务首次执行或迁移完成后自动出现。"
          style={{ marginBottom: 12 }}
        />
      )}

      {/* 多指标概览网格：当前读数 + 相对上一帧的环比（按指标语义分色） */}
      {summary?.available && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 8,
            marginBottom: 12,
          }}
        >
          {summary.items.map((item) => (
            <div
              key={item.key}
              style={{
                border: '1px solid var(--app-border)',
                borderRadius: 8,
                padding: '10px 12px',
              }}
            >
              <div style={{ fontSize: 12, color: 'var(--app-text-3)' }}>{item.label}</div>
              <div style={{ fontSize: 20, fontWeight: 600, lineHeight: '28px' }}>
                {item.value ?? '—'}
              </div>
              <div>{deltaTone(item)}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <Segmented
          size="small"
          value={metric}
          onChange={(value) => setMetric(value as string)}
          options={options.map((o) => ({ label: o.label, value: o.value }))}
        />
        <Space>
          {/* 快照归档导出（CSV，dashboard:view）；数据外带，后端写审计 */}
          <ExportButton
            url="/admin/export/ops-metrics"
            params={{}}
            label="指标快照"
            fallbackName="ops-metrics.csv"
          />
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
        </Space>
      </div>

      <TrendChart
        data={points}
        color="var(--app-purple)"
        valueLabel={currentLabel}
        loading={loading}
        rangeLabel={windowLabel}
      />

      {!loading && data?.available && (
        <div className="dash-trend__summary" style={{ marginTop: 8 }}>
          <span>
            {windowLabel}
            {currentLabel}当前读数 <strong>{latest ?? '—'}</strong>
          </span>
          <span className="dash-trend__summary-sub">共 {points.length} 帧快照</span>
        </div>
      )}
    </Card>
  )
}
