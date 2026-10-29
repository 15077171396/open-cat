import { Card, Tag, Tooltip, Typography } from 'antd'
import { FunnelPlotOutlined } from '@ant-design/icons'
import type { NormalizedFunnel } from '../../types'
import { formatPercent } from './dashboardUtils'

/**
 * 转化漏斗 + 留存卡。
 *
 * 存在意义：商业模式是「注册 → 免费 3 小时 → 星球认证 → VIP」，
 * 此前只有 `conversion_rate` 一个结果值，**中间四段完全不可见**，等于无法优化。
 * 这张卡把「哪一段在流失」直接摆出来。
 *
 * 三条显示约定（改之前先读）：
 * - `count === null` 显示「—」而不是 0：聚合失败 ≠ 没人走到这一步；
 * - `rateFromPrev` 才是定位流失点的关键（累计转化率会被前面的漏斗放大/掩盖）；
 * - 队列是「窗口内注册的人」，所以最后一天的 D1/D7 天然偏低（窗口还没过完），
 *   必须显式提示，否则运营会误判「最近质量突然变差」。
 */
interface Props {
  data: NormalizedFunnel
  loading?: boolean
  failed?: boolean
  onRefresh?: () => void
}

const titleStyle = { fontSize: 15, fontWeight: 600 } as const

/** 阶段配色：由深到浅表示漏斗收窄，最后一段（认证通过）用成功色 */
const STAGE_COLORS = ['#1677ff', '#2f9ee8', '#5cb8c4', '#95c96f', '#52c41a'] as const

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div style={{ minWidth: 132 }}>
      <div style={{ fontSize: 12, color: '#8c8c8c' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, lineHeight: '28px' }}>{value}</div>
      {hint ? <div style={{ fontSize: 12, color: '#8c8c8c' }}>{hint}</div> : null}
    </div>
  )
}

export default function FunnelPanel({ data, loading, failed, onRefresh }: Props) {
  const registered = data.stages[0]?.count ?? null
  const hasData = data.stages.some((s) => (s.count ?? 0) > 0)

  return (
    <Card
      className="dash-card"
      variant="outlined"
      title={
        <span style={titleStyle}>
          <FunnelPlotOutlined style={{ marginRight: 8, color: '#8c8c8c' }} />
          转化漏斗
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
            近 {data.days} 天注册队列{data.since ? `（自 ${data.since}）` : ''}
          </Typography.Text>
        </span>
      }
      extra={
        failed ? (
          <Tag color="error">数据异常</Tag>
        ) : loading ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            加载中…
          </Typography.Text>
        ) : (
          <Tooltip title="后一阶段只统计走完了前面所有阶段的人（嵌套口径）">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              嵌套口径
            </Typography.Text>
          </Tooltip>
        )
      }
    >
      {failed ? (
        <div
          style={{
            background: '#fff2f0',
            border: '1px solid #ffccc7',
            borderRadius: 6,
            padding: '8px 12px',
            fontSize: 13,
            color: '#a8071a',
            marginBottom: 12,
          }}
        >
          漏斗数据加载失败，下方数字可能不完整。
          {onRefresh ? (
            <Typography.Link style={{ marginLeft: 8 }} onClick={onRefresh}>
              重试
            </Typography.Link>
          ) : null}
        </div>
      ) : null}

      {!hasData && !loading ? (
        <div style={{ fontSize: 13, color: '#8c8c8c', padding: '8px 0' }}>
          近 {data.days} 天没有新注册，暂无可分析的队列。
        </div>
      ) : (
        <div>
          {data.stages.map((stage, index) => {
            const count = stage.count
            // 以「注册」为 100% 基准；注册数未知时所有条都按空处理，不编造比例
            const ratio = count !== null && registered ? Math.min(1, count / registered) : 0
            const color = STAGE_COLORS[Math.min(index, STAGE_COLORS.length - 1)]
            return (
              <div
                key={stage.key || stage.label}
                style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}
              >
                <div style={{ width: 92, fontSize: 13, color: '#595959', flex: '0 0 auto' }}>
                  {stage.label}
                </div>
                <div
                  style={{
                    flex: 1,
                    minWidth: 0,
                    height: 22,
                    background: '#f5f5f5',
                    borderRadius: 4,
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      width: `${Math.max(ratio * 100, count ? 1.5 : 0)}%`,
                      height: '100%',
                      background: color,
                      borderRadius: 4,
                      transition: 'width .3s ease',
                    }}
                  />
                </div>
                <div style={{ width: 128, textAlign: 'right', flex: '0 0 auto' }}>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>
                    {count === null ? '—' : count.toLocaleString('zh-CN')}
                  </span>
                  <span style={{ fontSize: 12, color: '#8c8c8c', marginLeft: 8 }}>
                    {index === 0 ? '人' : formatPercent(stage.rateFromPrev)}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 24,
          borderTop: '1px solid #f0f0f0',
          paddingTop: 12,
          marginTop: 4,
        }}
      >
        <Stat
          label="申请通过率"
          value={formatPercent(data.approvalRate)}
          hint="认证通过 / 提交申请"
        />
        <Stat
          label="注册→VIP 转化"
          value={formatPercent(data.paidRate)}
          hint="队列内最终成为 VIP 的比例"
        />
        <Stat label="队列人数" value={registered === null ? '—' : registered.toLocaleString('zh-CN')} />
      </div>

      {/* 留存：只统计「有真实使用行为」（下载/学习/工具），没有浏览埋点，故不叫「访问留存」 */}
      <div style={{ marginTop: 12, borderTop: '1px solid #f0f0f0', paddingTop: 12 }}>
        <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 6 }}>
          留存（近 {data.retentionDays} 天队列 · 活跃=有下载/学习/工具行为）
        </div>
        {data.retention.length === 0 ? (
          <div style={{ fontSize: 13, color: '#8c8c8c' }}>该窗口内没有注册，暂无留存样本。</div>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {data.retention.slice(-10).map((row) => (
              <Tooltip
                key={row.date}
                title={`队列 ${row.cohort} 人 · 次日活跃 ${row.d1Active ?? '—'} · 第 7 日活跃 ${row.d7Active ?? '—'}`}
              >
                <Tag style={{ marginInlineEnd: 0 }}>
                  {row.date.slice(5)}
                  <span style={{ marginLeft: 6, color: '#8c8c8c' }}>{row.cohort} 人</span>
                  <span style={{ marginLeft: 8 }}>D1 {formatPercent(row.d1Rate)}</span>
                  <span style={{ marginLeft: 6 }}>D7 {formatPercent(row.d7Rate)}</span>
                </Tag>
              </Tooltip>
            ))}
          </div>
        )}
        <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 6 }}>
          提示：最近一两天的 D1/D7 会偏低 —— 观察窗口还没过完，属正常现象。
        </div>
      </div>
    </Card>
  )
}
