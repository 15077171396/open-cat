import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Row, Statistic } from 'antd'
import { DatabaseOutlined, ReloadOutlined } from '@ant-design/icons'
import type { TableProps } from 'antd'
import { apiGet } from '../../api/client'
import { toast } from '../../utils/toast'
import { getErrorMessage } from '../../utils/error'
import { PageHeader, DataTable } from '../../components/ui'

interface CachePrefix {
  prefix: string
  count: number
}

interface CacheStats {
  available: boolean
  error: string | null
  version: string
  used_memory: number
  used_memory_human: string
  peak_memory_human: string
  maxmemory_human: string
  connected_clients: number
  total_keys: number
  hits: number
  misses: number
  hit_rate: number
  uptime_seconds: number
  scanned: number
  truncated: boolean
  prefixes: CachePrefix[]
}

function duration(seconds: number): string {
  if (!seconds) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  return days > 0 ? `${days} 天 ${hours} 小时` : `${hours} 小时`
}

/** Redis 缓存监控（INFO + 有限 SCAN，只读）。 */
export default function MonitorCache() {
  const [data, setData] = useState<CacheStats | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await apiGet<CacheStats>('/admin/monitor/cache'))
    } catch (error) {
      toast.error(getErrorMessage(error, '缓存指标加载失败'))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const columns: TableProps<CachePrefix>['columns'] = [
    { title: '键前缀', dataIndex: 'prefix', key: 'prefix' },
    { title: '数量', dataIndex: 'count', key: 'count', width: 120 },
  ]

  return (
    <div>
      <PageHeader
        icon={<DatabaseOutlined />}
        title="缓存监控"
        subtitle={
          data?.available
            ? `Redis ${data.version || ''} · 扫描 ${data.scanned} 个键${data.truncated ? '（已截断）' : ''}`
            : 'Redis INFO 与键空间分布（只读）'
        }
        actions={
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
        }
      />

      {data && !data.available && (
        <Alert
          type="warning"
          showIcon
          title="Redis 不可用"
          description={data.error || '请检查 Redis 服务与连接配置'}
        />
      )}

      {data?.available && (
        <>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={12} xl={6}>
              <Card className="monitor-metric" variant="outlined">
                <Statistic title="键总数" value={data.total_keys} />
                <div className="monitor-metric__detail">数据库当前键数量</div>
              </Card>
            </Col>
            <Col xs={24} md={12} xl={6}>
              <Card className="monitor-metric" variant="outlined">
                <Statistic title="已用内存" value={data.used_memory_human || '—'} />
                <div className="monitor-metric__detail">峰值 {data.peak_memory_human || '—'} · 上限 {data.maxmemory_human}</div>
              </Card>
            </Col>
            <Col xs={24} md={12} xl={6}>
              <Card className="monitor-metric" variant="outlined">
                <Statistic title="命中率" value={data.hit_rate} precision={1} suffix="%" />
                <div className="monitor-metric__detail">
                  命中 {data.hits} / 未命中 {data.misses}
                </div>
              </Card>
            </Col>
            <Col xs={24} md={12} xl={6}>
              <Card className="monitor-metric" variant="outlined">
                <Statistic title="连接客户端" value={data.connected_clients} />
                <div className="monitor-metric__detail">运行 {duration(data.uptime_seconds)}</div>
              </Card>
            </Col>
          </Row>

          <Card variant="outlined" style={{ marginTop: 16 }} title="键空间分布（Top 12）">
            <DataTable<CachePrefix>
              columns={columns}
              dataSource={data.prefixes}
              rowKey="prefix"
              scrollX={480}
              pagination={undefined}
              empty={{
                hasFilter: false,
                filteredText: '没有数据',
                emptyText: 'Redis 中没有可统计的键',
              }}
            />
          </Card>
        </>
      )}
    </div>
  )
}
