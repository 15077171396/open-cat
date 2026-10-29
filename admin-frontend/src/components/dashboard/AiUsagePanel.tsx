import { Button, Card, Tooltip } from 'antd'
import { InfoCircleOutlined, RobotOutlined } from '@ant-design/icons'
import type { NormalizedAiUsage } from '../../types'
import {
  buildModelShares,
  combineQuotaTone,
  formatCost,
  imageCostLabel,
  quotaState,
  shortDate,
  shortNumber,
} from './dashboardUtils'
import type { QuotaTone } from './dashboardUtils'
import TrendChart from './TrendChart'

/** AI 板块主色（与看板里 AI 相关的标签、折线保持一致） */
const AI_ACCENT = '#722ed1'
/** tokens 构成双色：输入用蓝、输出用青，和折线的紫区分开（后台是浅色主题） */
const TOKEN_INPUT_COLOR = '#1677ff'
const TOKEN_OUTPUT_COLOR = '#36cfc9'

const cardTitleStyle = { fontSize: 15, fontWeight: 600 } as const

/** 档位 → 徽标文案 */
const TONE_TEXT: Record<QuotaTone, string> = {
  unset: '护栏未启用',
  ok: '护栏正常',
  warn: '接近上限',
  danger: '已达上限',
}

/** 档位 → 徽标补充说明（鼠标悬浮可见，避免在卡片里堆长句） */
const TONE_HINT: Record<QuotaTone, string> = {
  unset: '全站日 / 月上限为 0（不限制），当前只统计用量',
  ok: '用量水位正常，低于上限的 80%',
  warn: '已用超过上限的 80%，建议关注调用来源',
  danger: '已达到上限，新的 AI 调用会被拒绝',
}

interface AiUsagePanelProps {
  data: NormalizedAiUsage
  loading: boolean
  /**
   * 用量接口是否取失败。失败时卡片里全是 0，与「真的一次都没调用」长得一样，
   * 不显式提示就会被当成「护栏很安全」——必须区分开。
   */
  failed?: boolean
  /** 有 setting:view 权限时才渲染「去配置」入口（否则点进去也是 403） */
  canConfig: boolean
  onConfig: () => void
}

/** 额度水位条：已用 / 上限 + 百分比进度 + 剩余 */
function QuotaMeter({
  label,
  used,
  limit,
  unit = '次',
  remaining,
  showConfig,
  onConfig,
}: {
  label: string
  used: number
  limit: number
  /** 计量单位：调用用「次」、插图用「张」 */
  unit?: string
  /** 后端下发的剩余额度；缺省时按 used / limit 推算 */
  remaining?: number | null
  showConfig: boolean
  onConfig: () => void
}) {
  const quota = quotaState(used, limit)
  const rest = remaining ?? quota.remaining
  // 有用量但比例极小时也给 2% 宽度，否则「用了一点」和「完全没用」在视觉上一样
  const width = quota.tone === 'unset' ? 0 : Math.max(quota.percent, used > 0 ? 2 : 0)

  return (
    <div className="ai-guard__meter">
      <div className="ai-guard__meter-head">
        <span className="ai-guard__meter-label">{label}</span>
        {quota.tone === 'unset' ? (
          <span className="ai-guard__meter-value ai-guard__meter-value--muted">未设上限</span>
        ) : (
          <span className="ai-guard__meter-value">
            {quota.used.toLocaleString('zh-CN')}
            <span className="ai-guard__meter-sep"> / </span>
            {quota.limit.toLocaleString('zh-CN')}
          </span>
        )}
      </div>
      <div
        className={
          quota.tone === 'unset'
            ? 'ai-guard__track ai-guard__track--unset'
            : 'ai-guard__track'
        }
        role="progressbar"
        aria-label={`${label}使用率`}
        aria-valuenow={quota.percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        {quota.tone !== 'unset' && (
          <div
            className={`ai-guard__fill ai-guard__fill--${quota.tone}`}
            style={{ width: `${width}%` }}
          />
        )}
      </div>
      <div className="ai-guard__meter-foot">
        {quota.tone === 'unset' ? (
          <>
            <span>该维度不限制</span>
            {showConfig && (
              <Button type="link" size="small" className="ai-guard__link" onClick={onConfig}>
                设上限
              </Button>
            )}
          </>
        ) : (
          <span>
            剩余 <strong>{rest?.toLocaleString('zh-CN')}</strong> {unit} · 已用{' '}
            {quota.percent}%
          </span>
        )}
      </div>
    </div>
  )
}

/** 今日 tokens 构成：输入 / 输出双色堆叠条 */
function TokenSplit({
  prompt,
  completion,
  total,
}: {
  prompt: number
  completion: number
  total: number
}) {
  const inputRatio = total > 0 ? Math.min(100, (prompt / total) * 100) : 0
  return (
    <div className="ai-guard__tokens">
      <div className="ai-guard__meter-head">
        <span className="ai-guard__meter-label">今日 Tokens</span>
        <span className="ai-guard__meter-value">{shortNumber(total)}</span>
      </div>
      <div className="ai-guard__split" aria-hidden="true">
        {total > 0 ? (
          <>
            <span
              className="ai-guard__split-part"
              style={{ width: `${inputRatio}%`, background: TOKEN_INPUT_COLOR }}
            />
            <span
              className="ai-guard__split-part"
              style={{ width: `${100 - inputRatio}%`, background: TOKEN_OUTPUT_COLOR }}
            />
          </>
        ) : (
          <span className="ai-guard__split-empty" />
        )}
      </div>
      <div className="ai-guard__legend">
        <span>
          <i className="ai-guard__dot" style={{ background: TOKEN_INPUT_COLOR }} />
          输入 {shortNumber(prompt)}
        </span>
        <span>
          <i className="ai-guard__dot" style={{ background: TOKEN_OUTPUT_COLOR }} />
          输出 {shortNumber(completion)}
        </span>
      </div>
    </div>
  )
}

/** 本月按模型明细：每行 = 模型名 + tokens + 金额 + 占比条 */
function ModelBreakdown({
  data,
}: {
  data: NormalizedAiUsage
}) {
  const shares = buildModelShares(
    data.month.by_model,
    data.month.unattributed,
    Object.keys(data.pricing.per_model),
  )

  if (shares.length === 0) {
    return (
      <div className="ai-guard__models">
        <div className="ai-guard__models-head">本月按模型</div>
        <div className="ai-guard__empty">本月还没有按模型的用量记录</div>
      </div>
    )
  }

  return (
    <div className="ai-guard__models">
      <div className="ai-guard__models-head">
        本月按模型
        <span className="ai-guard__models-count">{shares.length}</span>
      </div>
      {shares.map((row) => (
        <div className="ai-guard__model" key={row.key}>
          <div className="ai-guard__model-top">
            <span className="ai-guard__model-name" title={row.label}>
              {row.label}
            </span>
            {row.priced && (
              <Tooltip title="该模型在「系统 → 模型管理」里配置了专属单价">
                <span className="ai-guard__model-flag">专属价</span>
              </Tooltip>
            )}
            <span className="ai-guard__model-tokens">{shortNumber(row.tokens)}</span>
            <span className="ai-guard__model-cost">{formatCost(row.cost)}</span>
          </div>
          <div className="ai-guard__model-track">
            <i
              className={
                row.unattributed
                  ? 'ai-guard__model-fill ai-guard__model-fill--muted'
                  : 'ai-guard__model-fill'
              }
              style={{ width: `${Math.max(row.ratio * 100, 2)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * AI 用量与成本护栏（台账 P1-2 的看板入口）。
 *
 * 设计取向：**护栏的核心是「离上限还有多远」**，所以第一屏是两条水位条，
 * 而不是一排孤立的数字。旧版把 4 个 Statistic 塞在 14/24 的窄栏里，
 * 标签折行、金额位重复四遍「未配置单价」、底部还有三行浅灰说明文字。
 *
 * 现在：一张整宽卡片分三栏 —— 今日 / 本月 / 近 30 天趋势；
 * 「未配置」状态收敛成顶部一条提示 + 一处「去配置」入口，金额位统一显示「—」。
 */
export default function AiUsagePanel({
  data,
  loading,
  failed = false,
  canConfig,
  onConfig,
}: AiUsagePanelProps) {
  const todayQuota = quotaState(data.today.calls, data.limits.daily)
  const monthQuota = quotaState(data.month.calls, data.limits.monthly)
  // 插图限额是独立的护栏：只设了插图上限时，顶部不能再显示「不会拦截任何调用」
  const imageQuota = quotaState(data.today.images, data.limits.image_daily)
  const tone = combineQuotaTone([todayQuota.tone, monthQuota.tone, imageQuota.tone])
  const guardEnabled = tone !== 'unset'
  // 接口失败时状态徽标必须换成「数据异常」：否则「护栏未启用」会让人误判成护栏安全
  const badgeTone: QuotaTone = failed ? 'danger' : tone
  const badgeText = failed ? '数据异常' : TONE_TEXT[tone]
  const badgeHint = failed
    ? '用量接口读取失败，下方数值为占位 0，不代表当前没有调用'
    : TONE_HINT[tone]
  const trendPoints = data.trend.map((p) => ({ date: p.date, count: p.calls }))
  const trendTotal = data.trend.reduce((sum, p) => sum + p.calls, 0)
  const peak = data.trend.reduce(
    (best, p) => (p.calls > best.calls ? p : best),
    { date: '', calls: 0 },
  )

  return (
    <Card
      className="dash-card ai-guard"
      variant="outlined"
      title={
        <span style={cardTitleStyle}>
          <RobotOutlined style={{ marginRight: 8, color: '#8c8c8c' }} />
          AI 用量与成本护栏
        </span>
      }
      extra={
        <Tooltip title={badgeHint}>
          <span className={`ai-guard__badge ai-guard__badge--${badgeTone}`}>{badgeText}</span>
        </Tooltip>
      }
    >
      {failed && (
        <div className="ai-guard__notice ai-guard__notice--danger">
          <InfoCircleOutlined />
          <span>用量接口读取失败，下方数值为占位 0，不代表当前没有调用。请稍后刷新。</span>
        </div>
      )}

      {!guardEnabled && !failed && (
        <div className="ai-guard__notice">
          <InfoCircleOutlined />
          <span>
            全站日 / 月上限为 0（不限制），当前只统计用量，不会拦截任何调用。
          </span>
          {canConfig && (
            <Button
              type="link"
              size="small"
              className="ai-guard__notice-action"
              onClick={onConfig}
            >
              去设置上限
            </Button>
          )}
        </div>
      )}

      <div className="ai-guard__grid">
        {/* 一、今日 */}
        <section className="ai-guard__col">
          <div className="ai-guard__colhead">
            <span className="ai-guard__coltitle">今日</span>
            <span className="ai-guard__colmeta">UTC 日界</span>
          </div>

          <div className="ai-guard__hero">
            <span className="ai-guard__hero-value">
              {data.today.calls.toLocaleString('zh-CN')}
            </span>
            <span className="ai-guard__hero-unit">次调用</span>
          </div>

          <QuotaMeter
            label="今日额度"
            used={data.today.calls}
            limit={data.limits.daily}
            showConfig={canConfig && guardEnabled}
            onConfig={onConfig}
          />

          <QuotaMeter
            label="插图日限额"
            used={data.today.images}
            limit={data.limits.image_daily}
            unit="张"
            remaining={data.limits.remaining_images}
            showConfig={canConfig}
            onConfig={onConfig}
          />

          <TokenSplit
            prompt={data.today.prompt_tokens}
            completion={data.today.completion_tokens}
            total={data.today.total_tokens}
          />

          <div className="ai-guard__costs">
            <div className="ai-guard__cost">
              <span className="ai-guard__cost-label">今日插图</span>
              <span className="ai-guard__cost-value">
                {data.today.images.toLocaleString('zh-CN')} 张
              </span>
            </div>
            <div className="ai-guard__cost">
              <span className="ai-guard__cost-label">今日插图费用</span>
              <span
                className="ai-guard__cost-value"
                style={
                  data.today.image_cost === null
                    ? { fontSize: 13, color: '#8c8c8c', fontWeight: 400 }
                    : undefined
                }
              >
                {imageCostLabel(data.today.image_cost, failed)}
              </span>
            </div>
          </div>

          <div className="ai-guard__chips">
            <Tooltip title="超过全站或用户额度被拒绝的 AI 调用次数">
              <span
                className={
                  data.today.blocked > 0
                    ? 'ai-guard__chip ai-guard__chip--danger'
                    : 'ai-guard__chip'
                }
              >
                今日拦截 {data.today.blocked} 次
              </span>
            </Tooltip>
            <span className="ai-guard__chip ai-guard__chip--quiet">
              今日花费 {formatCost(data.today.estimated_cost)}
            </span>
          </div>
        </section>

        {/* 二、本月 */}
        <section className="ai-guard__col">
          <div className="ai-guard__colhead">
            <span className="ai-guard__coltitle">本月</span>
            <span className="ai-guard__colmeta">自然月</span>
          </div>

          <div className="ai-guard__hero">
            <span className="ai-guard__hero-value">
              {data.month.calls.toLocaleString('zh-CN')}
            </span>
            <span className="ai-guard__hero-unit">次调用</span>
          </div>

          <QuotaMeter
            label="本月额度"
            used={data.month.calls}
            limit={data.limits.monthly}
            showConfig={canConfig && guardEnabled}
            onConfig={onConfig}
          />

          <div className="ai-guard__costs">
            <div className="ai-guard__cost">
              <span className="ai-guard__cost-label">本月花费</span>
              <span className="ai-guard__cost-value">
                {formatCost(data.month.estimated_cost)}
              </span>
            </div>
            <div className="ai-guard__cost">
              <span className="ai-guard__cost-label">本月 Tokens</span>
              <span className="ai-guard__cost-value">{shortNumber(data.month.total_tokens)}</span>
            </div>
          </div>

          {!data.pricing.configured && (
            <div className="ai-guard__hint">
              单价未配置，金额暂无法估算
              {canConfig && (
                <Button
                  type="link"
                  size="small"
                  className="ai-guard__link"
                  onClick={onConfig}
                >
                  配置单价
                </Button>
              )}
            </div>
          )}

          <ModelBreakdown data={data} />
        </section>

        {/* 三、近 30 天调用量 */}
        <section className="ai-guard__col">
          <div className="ai-guard__colhead">
            <span className="ai-guard__coltitle">近 30 天</span>
            <span className="ai-guard__colmeta">
              共 {trendTotal} 次 · 日均 {(trendTotal / 30).toFixed(1)}
            </span>
          </div>
          <TrendChart
            data={trendPoints}
            color={AI_ACCENT}
            valueLabel="调用"
            rangeLabel="近 30 天"
            height={188}
            loading={loading}
          />
          {trendTotal > 0 && (
            <div className="ai-guard__chartfoot">
              <span>
                峰值 <strong>{peak.calls.toLocaleString('zh-CN')}</strong> 次
              </span>
              <span className="ai-guard__chartfoot-date">{shortDate(peak.date)}</span>
            </div>
          )}
        </section>
      </div>
    </Card>
  )
}
