import { useCallback, useEffect, useMemo, useState } from 'react'
import { Row, Col, Card, Typography, Button, Segmented, Result, Alert } from 'antd'
import {
  TeamOutlined,
  HddOutlined,
  ThunderboltOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
  RiseOutlined,
  AuditOutlined,
  CommentOutlined,
  ReloadOutlined,
  BookOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../api/client'
import { getApiErrorStatus } from '../utils/error'
import { useAuthStore } from '../store/authStore'
import type {
  AiUsageData,
  ContentAttributionData,
  DashboardData,
  FunnelOverviewData,
  SystemStatusData,
} from '../types'
import {
  normalizeAiUsage,
  normalizeAttribution,
  normalizeDashboard,
  normalizeFunnel,
  normalizeSystemStatus,
} from '../components/dashboard/dashboardUtils'
import MetricCard from '../components/dashboard/MetricCard'
import TrendChart from '../components/dashboard/TrendChart'
import DonutChart from '../components/dashboard/DonutChart'
import BarList from '../components/dashboard/BarList'
import AiUsagePanel from '../components/dashboard/AiUsagePanel'
import SystemStatusPanel from '../components/dashboard/SystemStatusPanel'
import FunnelPanel from '../components/dashboard/FunnelPanel'
import ContentAttributionPanel from '../components/dashboard/ContentAttributionPanel'
import type { BarItem } from '../components/dashboard/BarList'

/** 趋势图可切换的指标 */
type TrendMetric = 'download' | 'registration'

const TREND_METRICS: { value: TrendMetric; label: string; color: string }[] = [
  { value: 'download', label: '下载量', color: '#1677ff' },
  { value: 'registration', label: '新增注册', color: '#52c41a' },
]

/** 注册来源配色（与环形图、图例共用同一常量，避免两处写死不一致） */
const SOURCE_COLORS = { phone: '#1677ff', email: '#722ed1' } as const

/** 内容资产条形配色（按「内容体量级」由深到浅，便于一眼区分板块） */
const CONTENT_COLORS = {
  resources: '#1677ff',
  nodes: '#36cfc9',
  plugins: '#722ed1',
  tools: '#fa8c16',
  tutorials: '#52c41a',
} as const

const cardTitleStyle = { fontSize: 15, fontWeight: 600 } as const

export default function Dashboard() {
  const navigate = useNavigate()
  const { isSuper, permissions } = useAuthStore()

  const [raw, setRaw] = useState<DashboardData | null>(null)
  const [aiRaw, setAiRaw] = useState<AiUsageData | null>(null)
  const [aiFailed, setAiFailed] = useState(false)
  const [sysRaw, setSysRaw] = useState<SystemStatusData | null>(null)
  const [sysFailed, setSysFailed] = useState(false)
  const [funnelRaw, setFunnelRaw] = useState<FunnelOverviewData | null>(null)
  const [funnelFailed, setFunnelFailed] = useState(false)
  const [attrRaw, setAttrRaw] = useState<ContentAttributionData | null>(null)
  const [attrFailed, setAttrFailed] = useState(false)
  // 新接口 404 = 后端还是升级前的进程（最常见的原因：改了代码没重启后端）。
  // 单独识别出来给一句明确提示，否则用户只会看到一串「数据异常」而不知道要做什么。
  const [staleBackend, setStaleBackend] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [updatedAt, setUpdatedAt] = useState('')
  const [trendMetric, setTrendMetric] = useState<TrendMetric>('download')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    // 每次刷新都重置「后端是旧进程」的提示：否则重启后端后再点刷新，黄条会一直挂着
    setStaleBackend(false)

    // ⚡ 5 个接口**并发**发出（2026-10-27）。
    // 此前是 5 次串行 await：每个都是一次完整 HTTP 往返，生产环境（公网 + nginx + 2 worker）
    // 光排队就能多花 0.5~1.5s，而它们之间**没有任何数据依赖**。
    //
    // 但「并发」不等于「一起等」：主数据（/admin/dashboard）一到就结束骨架，
    // 否则只要有一块慢（漏斗冷缓存 / 归因 GROUP BY），整页骨架就会被它拖住。
    // 所以先 await 主数据、再 await 其余四块 —— 请求是同时发出的，只是渲染分两批。
    const settle = <T,>(p: Promise<T>) =>
      p.then(
        (value) => ({ ok: true as const, value }),
        (error: unknown) => ({ ok: false as const, error }),
      )

    const dashReq = settle(apiGet<DashboardData>('/admin/dashboard'))
    // `as const` 让 Promise.all 保留**元组**类型：否则数组元素被推断成四者的联合，
    // 后面 setFunnelRaw(union) 会直接类型报错（tsc 抓到的，vite build 不做类型检查）
    const restReq = [
      settle(apiGet<AiUsageData>('/admin/ai-usage')),
      settle(apiGet<SystemStatusData>('/admin/system-status')),
      settle(apiGet<FunnelOverviewData>('/admin/funnel?days=30')),
      settle(apiGet<ContentAttributionData>('/admin/content-attribution?days=30&limit=10')),
    ] as const

    const dash = await dashReq
    if (dash.ok) {
      setRaw(dash.value)
      setUpdatedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
    } else {
      // 新接口 404 = 后端还是升级前的进程（改了代码没重启后端），单独给一句明确提示
      if (getApiErrorStatus(dash.error) === 404) setStaleBackend(true)
      setError('看板数据加载失败，请检查网络或服务状态后重试')
    }
    setLoading(false)

    // 四块附加数据各自降级：失败只让自己的卡片显示「数据异常」，不拖垮整页
    const [ai, sys, funnel, attr] = await Promise.all(restReq)
    for (const result of [ai, sys, funnel, attr]) {
      if (!result.ok && getApiErrorStatus(result.error) === 404) setStaleBackend(true)
    }
    setAiRaw(ai.ok ? ai.value : null)
    setAiFailed(!ai.ok)
    setSysRaw(sys.ok ? sys.value : null)
    setSysFailed(!sys.ok)
    setFunnelRaw(funnel.ok ? funnel.value : null)
    setFunnelFailed(!funnel.ok)
    setAttrRaw(attr.ok ? attr.value : null)
    setAttrFailed(!attr.ok)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 菜单级权限判断：无对应权限的「待办入口」指标卡不做成可点击（点了会被 PermGuard 挡成 403）
  const can = useCallback(
    (code: string) => isSuper || (permissions?.includes(code) ?? false),
    [isSuper, permissions],
  )

  // 兜底归一化：接口缺增强字段时降级为 0/空数组，而不是整页崩掉
  const data = useMemo(() => normalizeDashboard(raw), [raw])

  // AI 用量同样先兜底：缺字段只表现为「显示 0」，不会让卡片崩掉
  const ai = useMemo(() => normalizeAiUsage(aiRaw), [aiRaw])

  // 系统状态同样先兜底：缺字段只表现为「未知」，不会让卡片崩掉
  const system = useMemo(() => normalizeSystemStatus(sysRaw), [sysRaw])

  // 漏斗同样先兜底：缺字段只表现为「—」，不会让卡片崩掉
  const funnel = useMemo(() => normalizeFunnel(funnelRaw), [funnelRaw])

  // 归因同样先兜底：缺字段只表现为「—」，不会让卡片崩掉
  const attribution = useMemo(() => normalizeAttribution(attrRaw), [attrRaw])

  const trend = useMemo(() => {
    const meta = TREND_METRICS.find((m) => m.value === trendMetric) ?? TREND_METRICS[0]
    return {
      meta,
      points: trendMetric === 'download' ? data.download_trend : data.registration_trend,
    }
  }, [trendMetric, data])

  const trendTotal = useMemo(
    () => trend.points.reduce((sum, p) => sum + p.count, 0),
    [trend.points],
  )

  const sourceItems = useMemo(
    () => [
      { label: '手机号注册', value: data.registration_source.phone, color: SOURCE_COLORS.phone },
      { label: '邮箱注册', value: data.registration_source.email, color: SOURCE_COLORS.email },
    ],
    [data.registration_source],
  )
  const sourceTotal = data.registration_source.phone + data.registration_source.email

  const contentItems = useMemo<BarItem[]>(
    () => [
      { key: 'resources', label: '资源', value: data.content_overview.resources, color: CONTENT_COLORS.resources },
      { key: 'nodes', label: '知识百科节点', value: data.content_overview.nodes, color: CONTENT_COLORS.nodes },
      { key: 'plugins', label: '扩展', value: data.content_overview.plugins, color: CONTENT_COLORS.plugins },
      { key: 'tools', label: '在线工具', value: data.content_overview.tools, color: CONTENT_COLORS.tools },
      { key: 'tutorials', label: '已发布教程', value: data.content_overview.tutorials, color: CONTENT_COLORS.tutorials },
    ],
    [data.content_overview],
  )
  const contentTotal = contentItems.reduce((sum, it) => sum + it.value, 0)

  const topItems = useMemo<BarItem[]>(
    () =>
      data.top_resources.map((r) => ({
        key: String(r.id),
        label: r.name,
        value: r.download_count,
        extra: `浏览 ${r.view_count}`,
      })),
    [data.top_resources],
  )

  if (error) {
    return (
      <Result
        status="warning"
        title="数据加载失败"
        subTitle={error}
        extra={
          <Button type="primary" onClick={load}>
            重试
          </Button>
        }
      />
    )
  }

  return (
    <div className="dashboard">
      {/* 页头：标题 + 数据新鲜度 + 手动刷新 */}
      <div className="dash-header">
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            数据看板
          </Typography.Title>
          <div className="dash-header__sub">
            <span>{updatedAt ? `更新于 ${updatedAt}` : '正在加载…'}</span>
            <span className="dash-header__dot">·</span>
            <span>服务端缓存 15 分钟，指标为 UTC 日界口径</span>
          </div>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading} disabled={loading}>
          刷新
        </Button>
      </div>

      {/* 第一行：今日核心指标 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            title="今日新增注册"
            value={data.today_registrations}
            icon={<TeamOutlined />}
            tone="blue"
            loading={loading}
            hint={
              <span>
                累计用户 <strong>{data.total_users}</strong> 人
              </span>
            }
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            title="今日下载量"
            value={data.today_downloads}
            icon={<HddOutlined />}
            tone="purple"
            loading={loading}
            hint={
              <span>
                今日工具调用 <strong>{data.today_tool_usage}</strong> 次
              </span>
            }
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            title="体验转化率"
            value={data.conversion_rate}
            suffix="%"
            icon={<ThunderboltOutlined />}
            tone="orange"
            loading={loading}
            hint="免费时长耗尽后升级 VIP 的比例"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            title="星球认证数"
            value={data.approved_count}
            icon={<SafetyCertificateOutlined />}
            tone="green"
            loading={loading}
            hint={
              <span>
                待审核 <strong>{data.pending.auth_requests}</strong> 条
              </span>
            }
          />
        </Col>
      </Row>

      {/* 第二行：规模与待办（待办卡按权限决定是否可点击） */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            title="总用户数"
            value={data.total_users}
            icon={<UserOutlined />}
            tone="geekblue"
            loading={loading}
            hint={
              <span>
                手机号 <strong>{data.registration_source.phone}</strong> · 邮箱{' '}
                <strong>{data.registration_source.email}</strong>
              </span>
            }
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            title="近 7 日活跃用户"
            value={data.active_users_7d}
            icon={<RiseOutlined />}
            tone="cyan"
            loading={loading}
            hint="近 7 天产生过下载行为的用户"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            title="待审核认证"
            value={data.pending.auth_requests}
            icon={<AuditOutlined />}
            tone="orange"
            loading={loading}
            onClick={can('auth_request:view') ? () => navigate('/auth-requests') : undefined}
            hint={can('auth_request:view') ? '点击前往实名审核' : '无审核权限'}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            title="待处理反馈"
            value={data.pending.feedbacks}
            icon={<CommentOutlined />}
            tone="red"
            loading={loading}
            onClick={can('feedback:view') ? () => navigate('/feedback') : undefined}
            hint={can('feedback:view') ? '点击前往用户反馈' : '无反馈查看权限'}
          />
        </Col>
      </Row>

      {staleBackend ? (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          title="后端接口不存在（404）—— 当前运行的多半是升级前的旧进程"
          description={
            <span style={{ fontSize: 13 }}>
              代码已更新，但正在运行的后端没有重启，所以新接口（漏斗 / 系统状态 / 内容归因）返回 404。
              重启后端后刷新本页即可；若提示缺表，再执行一次 <code>alembic upgrade head</code>。
            </span>
          }
        />
      ) : null}

      {/* 第三行：趋势 + 注册来源 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={16}>
          <Card
            className="dash-card"
            variant="outlined"
            title={<span style={cardTitleStyle}>近 7 天趋势</span>}
            extra={
              <Segmented
                size="small"
                value={trendMetric}
                onChange={(value) => setTrendMetric(value as TrendMetric)}
                options={TREND_METRICS.map((m) => ({ label: m.label, value: m.value }))}
              />
            }
          >
            <div className="dash-trend__summary">
              <span>
                近 7 天{trend.meta.label}合计 <strong>{trendTotal}</strong>
              </span>
              <span className="dash-trend__summary-sub">
                日均 {(trendTotal / 7).toFixed(1)}
              </span>
            </div>
            <TrendChart
              data={trend.points}
              color={trend.meta.color}
              valueLabel={trend.meta.label}
              loading={loading}
            />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card
            className="dash-card"
            variant="outlined"
            title={<span style={cardTitleStyle}>注册来源</span>}
            extra={<span className="dash-card__extra">共 {sourceTotal} 人</span>}
          >
            <DonutChart items={sourceItems} centerLabel="注册用户" loading={loading} />
          </Card>
        </Col>
      </Row>

      {/* 第四行：内容资产 + 下载榜 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={12}>
          <Card
            className="dash-card"
            variant="outlined"
            title={
              <span style={cardTitleStyle}>
                <BookOutlined style={{ marginRight: 8, color: '#8c8c8c' }} />
                内容资产总览
              </span>
            }
            extra={<span className="dash-card__extra">共 {contentTotal} 项</span>}
          >
            <BarList items={contentItems} loading={loading} emptyText="暂无内容" />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card
            className="dash-card"
            variant="outlined"
            title={
              <span style={cardTitleStyle}>
                <HddOutlined style={{ marginRight: 8, color: '#8c8c8c' }} />
                资源下载榜 Top 5
              </span>
            }
            extra={
              can('resource:view') ? (
                <Button type="link" size="small" onClick={() => navigate('/resources')}>
                  查看全部
                </Button>
              ) : null
            }
          >
            <BarList
              items={topItems}
              ranked
              loading={loading}
              emptyText="暂无下载记录"
            />
          </Card>
        </Col>
      </Row>

      {/* 第五行：转化漏斗 + 留存（商业模式的核心指标：哪一段在流失） */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <FunnelPanel data={funnel} loading={loading} failed={funnelFailed} onRefresh={load} />
        </Col>
      </Row>

      {/* 第六行：内容归因（哪篇内容真的带来付费意愿） */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <ContentAttributionPanel
            data={attribution}
            loading={loading}
            failed={attrFailed}
            onRefresh={load}
          />
        </Col>
      </Row>

      {/* 第七行：AI 用量与成本护栏（台账 P1-2）
          整宽单卡、内部三栏（今日 / 本月 / 近 30 天）：护栏的重点是「离上限还有多远」，
          拆成两张卡时水位与趋势互相看不见，窄栏里的 4 个 Statistic 还会把标签挤折行。 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <AiUsagePanel
            data={ai}
            loading={loading}
            failed={aiFailed}
            canConfig={can('setting:view')}
            onConfig={() => navigate('/settings')}
          />
        </Col>
      </Row>

      {/* 第八行（**页面最底部**）：系统状态（运维可见性）—— 与业务指标分开，出错时只影响自己这一块。
          放最下面的理由：它是「排查时才会看」的信息，不是每天要读的业务数字；
          放在业务指标中间会把真正的主指标挤开。定时任务明细已独立成「系统 → 定时任务」页，
          这里只留概览 + 入口。 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <SystemStatusPanel
            data={system}
            loading={loading}
            failed={sysFailed}
            onRefresh={load}
            onOpenJobs={() => navigate('/scheduled-jobs')}
          />
        </Col>
      </Row>
    </div>
  )
}
