import { memo, useMemo, useState } from 'react'
import type { MouseEvent } from 'react'
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

interface SliceHover {
  index: number
  x: number
  y: number
}

/**
 * 环形占比图（纯 SVG）。
 * 用 `stroke-dasharray` 画弧：每个切片是一条同样大小的圆，
 * 只把自己那一段画成实线，其余留空 —— 比手算 path 弧线简单且不会有浮点误差。
 *
 * 悬浮切片时：切片加粗并轻微外扩（对齐 ECharts 饼图的 emphasis），
 * 同时在指针附近显示 `名称: 数值 (百分比%)`——参考项目的环形图就是这种交互。
 */
function DonutChartInner({
  items,
  size = 168,
  thickness = 18,
  centerLabel = '总计',
  loading = false,
}: DonutChartProps) {
  const [hover, setHover] = useState<SliceHover | null>(null)
  const radius = (size - thickness) / 2
  const circumference = 2 * Math.PI * radius
  const center = size / 2

  const { slices, total } = useMemo(
    () => buildDonutSlices(items, circumference, SLICE_GAP),
    [items, circumference],
  )

  const hoveredItem = hover ? items[hover.index] : null
  const hoveredRatio = hoveredItem && total > 0 ? hoveredItem.value / total : 0

  const trackHover = (event: MouseEvent<SVGCircleElement>, index: number) => {
    const ring = event.currentTarget.ownerSVGElement?.parentElement
    if (!ring) return
    const rect = ring.getBoundingClientRect()
    const x = Math.min(Math.max(event.clientX - rect.left, 76), Math.max(76, size - 76))
    const y = Math.min(Math.max(event.clientY - rect.top, 34), Math.max(34, size - 10))
    setHover({ index, x, y })
  }

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
      <div
        className="dash-donut__ring"
        style={{ width: size, height: size }}
        onMouseLeave={() => setHover(null)}
      >
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
            {slices.map((s, index) => (
              <circle
                key={s.label}
                className={`dash-donut__slice${hover?.index === index ? ' is-active' : ''}`}
                cx={center}
                cy={center}
                r={radius}
                fill="none"
                stroke={s.color}
                style={{ strokeWidth: hover?.index === index ? thickness + 5 : thickness }}
                strokeDasharray={`${s.dash} ${Math.max(0, circumference - s.dash)}`}
                strokeDashoffset={s.offset}
                onMouseEnter={(event) => trackHover(event, index)}
                onMouseMove={(event) => trackHover(event, index)}
              />
            ))}
          </g>
        </svg>
        <div className="dash-donut__center">
          <div className="dash-donut__center-value">{total}</div>
          <div className="dash-donut__center-label">{centerLabel}</div>
        </div>
        {hoveredItem && (
          <div
            className="dash-donut__tip"
            style={{ left: hover?.x, top: hover?.y }}
            role="status"
          >
            {hoveredItem.label}: <strong>{hoveredItem.value}</strong> (
            {(hoveredRatio * 100).toFixed(1)}%)
          </div>
        )}
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
