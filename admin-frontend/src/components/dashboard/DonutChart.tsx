import { memo, useMemo } from 'react'
import { Skeleton } from 'antd'
import { buildDonutSlices } from './dashboardUtils'
import type { DonutInput } from './dashboardUtils'

interface DonutChartProps {
  items: DonutInput[]
  /** 圆环外径 */
  size?: number
  /** 圆环粗细 */
  thickness?: number
  /** 圆心数值下方的说明文字 */
  centerLabel?: string
  loading?: boolean
}

/** 切片之间的视觉间隙（弧长，px）。单切片时由 buildDonutSlices 自动置 0 */
const SLICE_GAP = 4

/**
 * 环形占比图（纯 SVG）。
 * 用 `stroke-dasharray` 画弧：每个切片是一条同样大小的圆，
 * 只把自己那一段画成实线，其余留空 —— 比手算 path 弧线简单且不会有浮点误差。
 */
function DonutChartInner({
  items,
  size = 168,
  thickness = 18,
  centerLabel = '总计',
  loading = false,
}: DonutChartProps) {
  const radius = (size - thickness) / 2
  const circumference = 2 * Math.PI * radius
  const center = size / 2

  const { slices, total } = useMemo(
    () => buildDonutSlices(items, circumference, SLICE_GAP),
    [items, circumference],
  )

  if (loading) {
    return (
      <div className="dash-donut">
        <Skeleton.Node active style={{ width: size, height: size, borderRadius: '50%' }} />
        <div className="dash-donut__legend">
          <Skeleton active paragraph={{ rows: 2 }} title={false} />
        </div>
      </div>
    )
  }

  return (
    <div className="dash-donut">
      <div className="dash-donut__ring" style={{ width: size, height: size }}>
        <svg width={size} height={size} role="img" aria-label={`占比图，${centerLabel} ${total}`}>
          {/* 底环：无论有没有数据都画，保证空态下圆环形态稳定不闪 */}
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke="#f5f5f5"
            strokeWidth={thickness}
          />
          {/* 从 12 点方向开始顺时针铺切片 */}
          <g transform={`rotate(-90 ${center} ${center})`}>
            {slices.map((s) => (
              <circle
                key={s.label}
                cx={center}
                cy={center}
                r={radius}
                fill="none"
                stroke={s.color}
                strokeWidth={thickness}
                strokeDasharray={`${s.dash} ${Math.max(0, circumference - s.dash)}`}
                strokeDashoffset={s.offset}
              />
            ))}
          </g>
        </svg>
        <div className="dash-donut__center">
          <div className="dash-donut__center-value">{total}</div>
          <div className="dash-donut__center-label">{centerLabel}</div>
        </div>
      </div>

      <ul className="dash-donut__legend">
        {items.map((it) => {
          const ratio = total > 0 ? it.value / total : 0
          return (
            <li key={it.label} className="dash-donut__legend-item">
              <span
                className="dash-donut__legend-dot"
                style={{ background: it.color }}
                aria-hidden="true"
              />
              <span className="dash-donut__legend-label">{it.label}</span>
              <span className="dash-donut__legend-value">{it.value}</span>
              <span className="dash-donut__legend-ratio">
                {total > 0 ? `${Math.round(ratio * 100)}%` : '—'}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export default memo(DonutChartInner)
