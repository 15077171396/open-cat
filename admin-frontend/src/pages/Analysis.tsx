import { useMemo, useState } from 'react'
import { Button, Card, Segmented, Space, Tag, Tooltip } from 'antd'
import { InfoCircleOutlined, ReloadOutlined, FallOutlined, RiseOutlined } from '@ant-design/icons'
import {
  RANGE_LABELS,
  buildAnalysisData,
  compactNumber,
} from '../components/analysis/mockData'
import type { AnalysisRange } from '../components/analysis/mockData'
import AnimatedNumber from '../components/analysis/AnimatedNumber'
import Sparkline from '../components/analysis/Sparkline'
import LineAreaChart from '../components/analysis/LineAreaChart'
import ColumnChart from '../components/analysis/ColumnChart'
import ScatterQuadrant from '../components/analysis/ScatterQuadrant'
import RadarChart from '../components/analysis/RadarChart'
import FunnelBars from '../components/analysis/FunnelBars'
import ContentRank from '../components/analysis/ContentRank'
import SourcePie from '../components/analysis/SourcePie'
import SearchBars from '../components/analysis/SearchBars'

const RANGE_OPTIONS: { label: string; value: AnalysisRange }[] = [
  { label: RANGE_LABELS['7d'], value: '7d' },
  { label: RANGE_LABELS['30d'], value: '30d' },
  { label: RANGE_LABELS['90d'], value: '90d' },
]

const INITIAL_SEED = 20261030

const cardTitle = (text: string) => <span className="ana-card__title">{text}</span>
const cardExtra = (text: string) => <span className="ana-card__extra">{text}</span>

/**
 * 数据分析页。
 *
 * 一整页**演示数据**：不请求后端、不写数据库，用于展示分析视图的布局与图表能力。
 * 数据由固定种子生成（同一范围反复进出页面数字不变），「换一批」只递增种子。
 * 接入真实指标时，把 `components/analysis/mockData.ts` 的 generator 换成接口数据即可，
 * 图表组件本身不依赖假数据的形状以外的任何假设。
 */
export default function Analysis() {
  const [range, setRange] = useState<AnalysisRange>('30d')
  const [seed, setSeed] = useState(INITIAL_SEED)
  const data = useMemo(() => buildAnalysisData(seed, range), [seed, range])

  return (
    <div className="ana-page">
      <header className="ana-head">
        <div>
          <div className="ana-eyebrow">CONTENT INTELLIGENCE</div>
          <h1 className="ana-title">数据分析</h1>
          <p className="ana-sub">
            阅读、用户与转化的一体化视图
            <Tooltip title="所有数字由浏览器本地生成，不读取数据库，也不写入任何业务表">
              <Tag className="ana-demo-tag" icon={<InfoCircleOutlined />} color="gold">
                演示数据
              </Tag>
            </Tooltip>
          </p>
        </div>
        <Space className="ana-controls" wrap size={10}>
          <Segmented
            value={range}
            options={RANGE_OPTIONS}
            onChange={(value) => setRange(value as AnalysisRange)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => setSeed((value) => value + 1)}>
            换一批
          </Button>
        </Space>
      </header>

      <section className="ana-hero" aria-label="核心指标">
        <div className="ana-hero__grid">
          {data.kpis.map((kpi) => {
            const up = kpi.delta >= 0
            return (
              <article className="ana-kpi" key={kpi.key}>
                <div className="ana-kpi__top">
                  <span className="ana-kpi__label">{kpi.label}</span>
                  <span className={`ana-kpi__delta${up ? ' is-up' : ' is-down'}`}>
                    {up ? <RiseOutlined aria-hidden="true" /> : <FallOutlined aria-hidden="true" />}
                    {kpi.deltaLabel}
                  </span>
                </div>
                <div className="ana-kpi__value">
                  <AnimatedNumber value={kpi.numeric} format={kpi.format} />
                  {kpi.unit ? <small>{kpi.unit}</small> : null}
                </div>
                <div className="ana-kpi__spark">
                  <Sparkline key={kpi.spark.join(',')} values={kpi.spark} color={kpi.color} />
                </div>
              </article>
            )
          })}
        </div>
        <div className="ana-hero__foot">
          <span>统计口径：{RANGE_LABELS[range]} · 演示数据</span>
          <span>生成于 {data.generatedAt}</span>
        </div>
      </section>

      <div className="ana-grid ana-grid--trend">
        <Card
          className="ana-card"
          variant="outlined"
          title={cardTitle('阅读趋势')}
          extra={cardExtra(`PV ${compactNumber(data.pvTotal)} · UV ${compactNumber(data.uvTotal)}`)}
        >
          <LineAreaChart
            key={`trend-${range}-${seed}`}
            labels={data.trend.map((point) => point.label)}
            series={[
              {
                key: 'pv',
                name: '阅读量',
                color: 'var(--app-primary)',
                data: data.trend.map((point) => point.pv),
                area: true,
              },
              {
                key: 'uv',
                name: '访客数',
                color: '#0EA5A4',
                data: data.trend.map((point) => point.uv),
              },
            ]}
            ariaLabel={`${RANGE_LABELS[range]}阅读量与访客数趋势`}
          />
        </Card>

        <Card
          className="ana-card ana-card--source"
          variant="outlined"
          title={cardTitle('流量来源')}
          extra={cardExtra('按访客')}
        >
          <SourcePie key={`source-${range}-${seed}`} items={data.sources} />
        </Card>
      </div>

      <div className="ana-grid ana-grid--two">
        <Card
          className="ana-card"
          variant="outlined"
          title={cardTitle('内容表现')}
          extra={cardExtra('Top 6 · 按阅读量')}
        >
          <ContentRank key={`rank-${range}-${seed}`} rows={data.contents} />
        </Card>
        <Card
          className="ana-card"
          variant="outlined"
          title={cardTitle('内容四象限')}
          extra={cardExtra('阅读量 × 完读率')}
        >
          <ScatterQuadrant
            key={`quadrant-${range}-${seed}`}
            points={data.quadrant}
            ariaLabel="内容阅读量与完读率四象限分布"
          />
        </Card>
      </div>

      <div className="ana-grid ana-grid--three">
        <Card
          className="ana-card"
          variant="outlined"
          title={cardTitle('新老用户')}
          extra={cardExtra('近 7 天')}
        >
          <ColumnChart
            key={`cohorts-${seed}`}
            labels={data.cohorts.map((point) => point.label)}
            series={[
              {
                key: 'new',
                name: '新用户',
                color: 'var(--app-primary)',
                data: data.cohorts.map((point) => point.newUsers),
              },
              {
                key: 'returning',
                name: '回访用户',
                color: '#0EA5A4',
                data: data.cohorts.map((point) => point.returning),
              },
            ]}
            stacked
            height={214}
            ariaLabel="近 7 天新用户与回访用户堆叠柱状图"
          />
        </Card>
        <Card
          className="ana-card"
          variant="outlined"
          title={cardTitle('活跃时段')}
          extra={cardExtra('每小时阅读')}
        >
          <ColumnChart
            key={`hourly-${seed}`}
            labels={data.hourly.map((_, hour) => `${hour}时`)}
            series={[
              { key: 'reads', name: '阅读量', color: '#D97706', data: data.hourly },
            ]}
            height={214}
            ariaLabel="24 小时阅读量分布"
          />
        </Card>
        <Card
          className="ana-card"
          variant="outlined"
          title={cardTitle('转化漏斗')}
          extra={cardExtra('访问 → 会员')}
        >
          <FunnelBars key={`funnel-${range}-${seed}`} stages={data.funnel} />
        </Card>
      </div>

      <div className="ana-grid ana-grid--two">
        <Card
          className="ana-card"
          variant="outlined"
          title={cardTitle('内容质量雷达')}
          extra={cardExtra('本月 vs 上月')}
        >
          <RadarChart
            key={`radar-${seed}`}
            indicators={data.radar.indicators}
            series={data.radar.series}
            ariaLabel="内容质量五项指标本月与上月对比雷达图"
          />
        </Card>
        <Card
          className="ana-card"
          variant="outlined"
          title={cardTitle('搜索热词')}
          extra={cardExtra('近 30 天')}
        >
          <SearchBars key={`search-${seed}`} terms={data.searchTerms} />
        </Card>
      </div>

      <p className="ana-foot-note">
        本页所有图表由前端假数据渲染（<code>src/components/analysis/mockData.ts</code>），
        不会写入数据库；种子 #{data.seed}，点「换一批」或切换时间范围会生成另一组数据。
      </p>
    </div>
  )
}
