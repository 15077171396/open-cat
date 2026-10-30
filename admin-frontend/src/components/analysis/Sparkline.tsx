import { useId, useMemo } from 'react'

interface SparklineProps {
  values: number[]
  color: string
  width?: number
  height?: number
}

/**
 * KPI 卡底部的迷你趋势线。没有坐标轴、没有交互 —— 只负责让「最近在涨还是在跌」
 * 一眼可见；精确数值由卡片主数字与右上角涨跌标签承担。
 */
export default function Sparkline({ values, color, width = 118, height = 38 }: SparklineProps) {
  const rawId = useId()
  const gradientId = `ana-spark-${rawId.replace(/[^a-zA-Z0-9]/g, '')}`

  const { line, area } = useMemo(() => {
    if (values.length === 0) return { line: '', area: '' }
    const min = Math.min(...values)
    const max = Math.max(...values)
    const span = max - min || 1
    const pad = 3
    const points = values.map((value, index) => {
      const x = values.length === 1 ? width / 2 : pad + (index / (values.length - 1)) * (width - pad * 2)
      const y = pad + (1 - (value - min) / span) * (height - pad * 2)
      return { x, y }
    })
    const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')
    const areaPath = `${linePath} L${points[points.length - 1].x.toFixed(2)},${height} L${points[0].x.toFixed(2)},${height} Z`
    return { line: linePath, area: areaPath }
  }, [values, width, height])

  if (!line) return null

  return (
    <svg
      className="ana-spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`趋势：${values.map((v) => Math.round(v)).join('、')}`}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.34} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path className="ana-spark__area" d={area} fill={`url(#${gradientId})`} />
      <path
        className="ana-spark__line"
        d={line}
        pathLength={1}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
