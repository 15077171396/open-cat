import { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { Empty, Skeleton } from 'antd'
import type { DashboardTrendPoint } from '../../types'
import {
  CHART_PADDING,
  baselineY,
  buildAreaPath,
  buildLinePath,
  hitTestIndex,
  labelStepFor,
  niceMax,
  niceTicks,
  plotSize,
  scaleTrend,
  shortDate,
} from './dashboardUtils'

interface TrendChartProps {
  data: DashboardTrendPoint[]
  /** 主色（线、点、渐变都用它） */
  color: string
  /** 悬浮提示里的指标名，如「下载量」 */
  valueLabel: string
  height?: number
  loading?: boolean
  /**
   * 区间文案，用于无障碍标签与「全零」提示（如「近 7 天」「近 30 天」）。
   * ⚠️ 必须由调用方传入真实区间：组件本身不知道数据跨度，
   * 写死「近 7 天」会让 30 天的图念错区间（读屏用户会拿到错误信息）。
   */
  rangeLabel?: string
}

/**
 * 测量元素宽度（ResizeObserver + window.resize 兜底）。
 * 不用 viewBox 自缩放：那会把 12px 坐标轴文字一起拉伸变糊。
 * ⚠️ ref 必须挂到所有渲染分支的同一个容器上：effect 只在挂载后测一次，
 * 若首帧走不带 ref 的骨架分支，宽度会永久停在 0（表现为数据有、图不画）。
 */
function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => {
      const next = el.clientWidth
      // 相同值直接跳过：ResizeObserver 在滚动/布局阶段会高频回调，
      // 不做这层拦截会引发「setState → 重排 → 再回调」的抖动
      setWidth((prev) => (prev === next ? prev : next))
    }
    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return [ref, width] as const
}

/** 通用趋势折线图（纯 SVG 手写，避免引入图表库给入口 chunk 增加 100KB 级体积） */
function TrendChartInner({
  data,
  color,
  valueLabel,
  height = 248,
  loading = false,
  rangeLabel = '近 7 天',
}: TrendChartProps) {
  const [wrapRef, width] = useElementWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  // useId 必须在组件顶层调用（useMemo 回调里会在后续渲染中「消失」，触发 hook 数量错误）
  const rawId = useId()
  // useId 返回值含冒号，直接当 SVG id 会在 url(#...) 解析异常，剥掉非字母数字字符
  const gradientId = `dash-trend-grad-${rawId.replace(/[^a-zA-Z0-9]/g, '')}`

  const values = useMemo(() => (data ?? []).map((d) => d.count), [data])
  const max = useMemo(() => (values.length ? Math.max(...values) : 0), [values])
  const top = useMemo(() => niceMax(max), [max])
  const ticks = useMemo(() => niceTicks(max), [max])
  const points = useMemo(
    () => scaleTrend(values, width, height, max),
    [values, width, height, max],
  )

  const { plotW, plotH } = plotSize(width, height)
  const base = baselineY(height)
  const linePath = useMemo(() => buildLinePath(points), [points])
  const areaPath = useMemo(() => buildAreaPath(points, base), [points, base])
  const total = useMemo(() => values.reduce((a, b) => a + b, 0), [values])

  // X 轴文案密度：按实际密度抽样（30 天窄图里 5~6 个标签，7 天宽图仍全显示）
  const labelStep = labelStepFor(values.length, plotW)

  const handleMove = useCallback(
    (e: MouseEvent<SVGRectElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      setHover(hitTestIndex(e.clientX - rect.left, points.length, plotW))
    },
    [points.length, plotW],
  )

  const handleLeave = useCallback(() => setHover(null), [])

  if (loading) {
    return (
      <div className="dash-chart" ref={wrapRef} style={{ height }}>
        <Skeleton active paragraph={{ rows: 4 }} />
      </div>
    )
  }

  if (values.length === 0) {
    return (
      <div className="dash-chart" ref={wrapRef} style={{ height }}>
        <div className="dash-chart__state">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />
        </div>
      </div>
    )
  }

  const activePoint = hover !== null ? points[hover] : undefined
  const tipLeft = activePoint
    ? Math.min(Math.max(activePoint.x, 76), Math.max(76, width - 76))
    : 0

  return (
    <div className="dash-chart" ref={wrapRef} style={{ height }}>
      {width > 0 && (
        <>
          <svg
            width={width}
            height={height}
            role="img"
            aria-label={`${rangeLabel}${valueLabel}趋势，共 ${total}`}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.24} />
                <stop offset="100%" stopColor={color} stopOpacity={0.01} />
              </linearGradient>
            </defs>

            {/* 横向网格线与 Y 轴刻度 */}
            {ticks.map((tick) => {
              const y = CHART_PADDING.top + plotH * (1 - tick / top)
              return (
                <g key={tick}>
                  <line
                    x1={CHART_PADDING.left}
                    y1={y}
                    x2={width - CHART_PADDING.right}
                    y2={y}
                    stroke="var(--app-border-light)"
                    strokeWidth={1}
                  />
                  <text
                    x={CHART_PADDING.left - 10}
                    y={y}
                    textAnchor="end"
                    dominantBaseline="middle"
                    fontSize={12}
                    fill="var(--app-text-4)"
                  >
                    {tick}
                  </text>
                </g>
              )
            })}

            {/* 面积 + 折线 */}
            <path d={areaPath} fill={`url(#${gradientId})`} />
            <path
              d={linePath}
              fill="none"
              stroke={color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />

            {/* 悬浮竖向指示线 */}
            {activePoint && (
              <line
                x1={activePoint.x}
                y1={CHART_PADDING.top}
                x2={activePoint.x}
                y2={base}
                stroke={color}
                strokeWidth={1}
                strokeDasharray="4 4"
                opacity={0.5}
              />
            )}

            {/* 数据点 */}
            {points.map((p) => (
              <circle
                key={p.index}
                cx={p.x}
                cy={p.y}
                r={hover === p.index ? 5 : 3.5}
                fill="var(--app-card)"
                stroke={color}
                strokeWidth={2}
              />
            ))}

            {/* X 轴日期 */}
            {points.map((p) =>
              p.index % labelStep === 0 ? (
                <text
                  key={`x-${p.index}`}
                  x={p.x}
                  y={base + 20}
                  textAnchor="middle"
                  fontSize={12}
                  fill="var(--app-text-3)"
                >
                  {shortDate(data[p.index]?.date ?? '')}
                </text>
              ) : null,
            )}

            {/* 透明命中层：覆盖整个绘图区，用于悬浮取最近点 */}
            <rect
              x={CHART_PADDING.left}
              y={CHART_PADDING.top}
              width={plotW}
              height={plotH}
              fill="transparent"
              onMouseMove={handleMove}
              onMouseLeave={handleLeave}
            />
          </svg>

          {/* 全零提示：折线会贴在基线上，不解释容易被误认为「图挂了」 */}
          {total === 0 && <div className="dash-chart__empty-hint">{rangeLabel}暂无记录</div>}

          {activePoint && (
            <div
              className="dash-chart__tip"
              style={{ left: tipLeft, top: activePoint.y - 14 }}
              role="status"
            >
              <div className="dash-chart__tip-label">
                {data[activePoint.index]?.date ?? ''}
              </div>
              <div className="dash-chart__tip-value">
                {valueLabel} {activePoint.value}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default memo(TrendChartInner)
