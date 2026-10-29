import { memo } from 'react'
import { Card, Skeleton } from 'antd'
import type { KeyboardEvent, ReactNode } from 'react'

/** 看板指标卡色调，对应 custom.css 的 .dash-metric__icon--<tone> 规则（新增须同步补 CSS） */
type MetricTone =
  | 'blue'
  | 'purple'
  | 'cyan'
  | 'green'
  | 'orange'
  | 'red'
  | 'geekblue'

interface MetricCardProps {
  title: string
  /** 主数值（已是可展示形态，如字符串 '40%' 或数字 12） */
  value: ReactNode
  /** 左侧图标 */
  icon: ReactNode
  tone?: MetricTone
  /** 数值后缀，如 '%'（字号更小、颜色更淡） */
  suffix?: ReactNode
  /** 底部补充说明；传 falsy 则该行不渲染 */
  hint?: ReactNode
  /** 是否加载中（渲染骨架，保持栅格高度稳定不跳动） */
  loading?: boolean
  /** 传入即成为可点击卡片（带 hover 抬升与键盘可达性） */
  onClick?: () => void
}

/**
 * 看板指标卡。
 * 用 `memo` 包一层：看板有 8 张卡，父级每次刷新都会重建 JSX，
 * 没有 memo 时 8 张卡会全部重渲染（图标节点是新建的，props 引用必变）。
 */
function MetricCardInner({
  title,
  value,
  icon,
  tone = 'blue',
  suffix,
  hint,
  loading = false,
  onClick,
}: MetricCardProps) {
  if (loading) {
    return (
      <Card className="dash-card dash-metric">
        <Skeleton active title={{ width: '52%' }} paragraph={{ rows: 1, width: '70%' }} />
      </Card>
    )
  }

  const clickable = typeof onClick === 'function'

  const handleKeyDown = clickable
    ? (e: KeyboardEvent<HTMLDivElement>) => {
        // 只接管 Enter / 空格：其余按键（Tab 等）保持原生行为，不破坏键盘导航
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick?.()
        }
      }
    : undefined

  return (
    <Card
      className={`dash-card dash-metric${clickable ? ' dash-metric--clickable' : ''}`}
      onClick={onClick}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={handleKeyDown}
      aria-label={clickable ? `${title}：${typeof value === 'number' || typeof value === 'string' ? value : ''}` : undefined}
    >
      <div className="dash-metric__body">
        <div className={`dash-metric__icon dash-metric__icon--${tone}`} aria-hidden="true">
          {icon}
        </div>
        <div className="dash-metric__main">
          <div className="dash-metric__title">{title}</div>
          <div className="dash-metric__value">
            {value}
            {suffix ? <span className="dash-metric__suffix">{suffix}</span> : null}
          </div>
        </div>
      </div>
      {hint ? <div className="dash-metric__hint">{hint}</div> : null}
    </Card>
  )
}

export default memo(MetricCardInner)
