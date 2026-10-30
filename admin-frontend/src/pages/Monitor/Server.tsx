import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Descriptions, Progress, Row, Space, Statistic } from 'antd'
import { CloudServerOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiGet } from '../../api/client'
import { toast } from '../../utils/toast'
import { getErrorMessage } from '../../utils/error'
import { PageHeader } from '../../components/ui'

interface ServerStats {
  available: boolean
  error: string | null
  platform: string
  python_version: string
  cpu_count: number
  cpu_percent: number
  load_avg: number[] | null
  memory_total: number
  memory_used: number
  memory_percent: number
  swap_total: number
  swap_used: number
  swap_percent: number
  disk_total: number
  disk_used: number
  disk_percent: number
  boot_time: number
  uptime_seconds: number
  process_pid: number
  process_memory: number
  process_threads: number
}

function bytes(value: number): string {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

function duration(seconds: number): string {
  if (!seconds) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days} 天 ${hours} 小时`
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  return `${minutes} 分`
}

function MetricCard({
  title,
  percent,
  detail,
}: {
  title: string
  percent: number
  detail: string
}) {
  const status = percent >= 90 ? 'exception' : percent >= 75 ? 'active' : 'normal'
  return (
    <Card className="monitor-metric" variant="outlined">
      <Statistic title={title} value={percent} precision={1} suffix="%" />
      <Progress percent={Math.min(100, Math.max(0, percent))} status={status} showInfo={false} />
      <div className="monitor-metric__detail">{detail}</div>
    </Card>
  )
}

/** 宿主机资源监控（psutil，只读）。 */
export default function MonitorServer() {
  const [data, setData] = useState<ServerStats | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await apiGet<ServerStats>('/admin/monitor/server'))
    } catch (error) {
      toast.error(getErrorMessage(error, '服务器指标加载失败'))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div>
      <PageHeader
        icon={<CloudServerOutlined />}
        title="服务器监控"
        subtitle="宿主机 CPU / 内存 / 磁盘 / 进程（只读，psutil 采集）"
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
          title="无法读取服务器指标"
          description={data.error || 'psutil 不可用'}
        />
      )}

      {data?.available && (
        <>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={12} xl={6}>
              <MetricCard
                title="CPU 使用率"
                percent={data.cpu_percent}
                detail={`${data.cpu_count} 核 · 负载 ${
                  data.load_avg ? data.load_avg.join(' / ') : '不可用'
                }`}
              />
            </Col>
            <Col xs={24} md={12} xl={6}>
              <MetricCard
                title="内存使用率"
                percent={data.memory_percent}
                detail={`${bytes(data.memory_used)} / ${bytes(data.memory_total)}`}
              />
            </Col>
            <Col xs={24} md={12} xl={6}>
              <MetricCard
                title="磁盘使用率"
                percent={data.disk_percent}
                detail={`${bytes(data.disk_used)} / ${bytes(data.disk_total)}`}
              />
            </Col>
            <Col xs={24} md={12} xl={6}>
              <MetricCard
                title="Swap 使用率"
                percent={data.swap_percent}
                detail={`${bytes(data.swap_used)} / ${bytes(data.swap_total)}`}
              />
            </Col>
          </Row>

          <Card className="monitor-detail" variant="outlined" style={{ marginTop: 16 }}>
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Descriptions title="运行环境" column={{ xs: 1, md: 2, xl: 3 }} size="small">
                <Descriptions.Item label="操作系统">{data.platform}</Descriptions.Item>
                <Descriptions.Item label="Python">{data.python_version}</Descriptions.Item>
                <Descriptions.Item label="运行时长">{duration(data.uptime_seconds)}</Descriptions.Item>
                <Descriptions.Item label="进程 PID">{data.process_pid}</Descriptions.Item>
                <Descriptions.Item label="进程内存">{bytes(data.process_memory)}</Descriptions.Item>
                <Descriptions.Item label="线程数">{data.process_threads}</Descriptions.Item>
              </Descriptions>
            </Space>
          </Card>
        </>
      )}
    </div>
  )
}
