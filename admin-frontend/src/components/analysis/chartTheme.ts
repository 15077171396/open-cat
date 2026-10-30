import type { EChartsOption } from 'echarts'

/** 系统要求减少动态时，ECharts 也不做动画（option.animation = false） */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/* 颜色一律用 CSS 变量：SVG 渲染器会原样写进 fill/stroke，亮暗主题自动跟随 */
export const AXIS_LABEL = { color: 'var(--app-text-3)', fontSize: 11 } as const

export const SPLIT_LINE = {
  lineStyle: { color: 'var(--app-border-light)' },
} as const

export const TOOLTIP_BASE = {
  backgroundColor: 'var(--app-elevated)',
  borderColor: 'var(--app-border)',
  borderWidth: 1,
  padding: [8, 10] as [number, number],
  textStyle: { color: 'var(--app-text-2)', fontSize: 12 },
  extraCssText: 'border-radius:10px;box-shadow:0 10px 26px rgba(15,23,42,.16);',
} as const

export const LEGEND_BASE = {
  icon: 'circle',
  itemWidth: 8,
  itemHeight: 8,
  itemGap: 14,
  textStyle: { color: 'var(--app-text-2)', fontSize: 11 },
} as const

/** 分析页统一动画参数（来自参考项目 useChart.getAnimationConfig） */
export const SERIES_ANIMATION = {
  animationDelay: (index: number) => 200 + index * 50,
  animationDuration: (index: number) => 1500 - index * 50,
  animationEasing: 'quarticOut' as const,
}

export function withAnimation(option: EChartsOption): EChartsOption {
  return { ...option, animation: !prefersReducedMotion() }
}
