import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import type { EChartsOption } from 'echarts'
import './echartsSetup'
import {
  AXIS_LABEL,
  LEGEND_BASE,
  SPLIT_LINE,
  TOOLTIP_BASE,
  prefersReducedMotion,
} from './chartTheme'
import { compactNumber } from './mockData'

export interface LineSeries {
  key: string
  name: string
  color: string
  data: number[]
  area?: boolean
}

interface LineAreaChartProps {
  labels: string[]
  series: LineSeries[]
  height?: number
  ariaLabel: string
}

function buildOption(
  labels: string[],
  series: LineSeries[],
  data: number[][],
  animation: boolean,
): EChartsOption {
  return {
    animation,
    animationDuration: 1300,
    animationDurationUpdate: 1300,
    animationEasing: 'cubicOut',
    color: series.map((item) => item.color),
    grid: { left: 44, right: 16, top: series.length > 1 ? 36 : 16, bottom: 28 },
    legend:
      series.length > 1
        ? {
            ...LEGEND_BASE,
            top: 0,
            left: 0,
            data: series.map((item) => item.name),
          }
        : undefined,
    tooltip: {
      ...TOOLTIP_BASE,
      trigger: 'axis',
      valueFormatter: (value: unknown) => compactNumber(Number(value)),
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: labels,
      axisLabel: AXIS_LABEL,
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0,
      axisLabel: {
        ...AXIS_LABEL,
        formatter: (value: number) => compactNumber(value),
      },
      splitLine: SPLIT_LINE,
      axisLine: { show: false },
      axisTick: { show: false },
    },
    series: series.map((item, index) => ({
      name: item.name,
      type: 'line',
      smooth: true,
      symbol: 'none',
      showSymbol: false,
      data: data[index],
      lineStyle: { width: 2, color: item.color },
      itemStyle: { color: item.color },
      areaStyle: item.area
        ? {
            // 用 opacity 而不是拼接 alpha 十六进制：系列色现在是 CSS 变量，
            // `${color}38` 会变成非法颜色（var(--x)38）
            color: item.color,
            opacity: 0.16,
          }
        : undefined,
      emphasis: { focus: 'series', lineStyle: { width: 3 } },
    })),
  }
}

/**
 * 折线图（ECharts 实现，对齐参考项目 FaLineChart 的入场方式）：
 * 先把所有系列数据置 0，再按系列错峰（100ms + 200ms × index）切到真实值，
 * 由 ECharts 用 1300ms cubicOut 把线从基线拉起来；单系列立即开始。
 */
export default function LineAreaChart({
  labels,
  series,
  height = 276,
  ariaLabel,
}: LineAreaChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = echarts.init(el, undefined, {
      renderer: 'svg',
      width: el.clientWidth || 600,
      height,
    })
    const reduce = prefersReducedMotion()
    const real = series.map((item) => [...item.data])
    let animated = series.map((item) => item.data.map(() => 0))

    chart.setOption(buildOption(labels, series, animated, false), { notMerge: true })

    const timers: number[] = []
    if (reduce) {
      chart.setOption(buildOption(labels, series, real, false), { notMerge: true })
    } else {
      series.forEach((_, index) => {
        const begin = series.length === 1 ? 0 : 100 + index * 200
        timers.push(
          window.setTimeout(() => {
            animated = animated.map((values, i) => (i === index ? real[i] : values))
            // 更新动画：ECharts 会把该系列从 0 平滑拉到真实值
            chart.setOption(buildOption(labels, series, animated, true))
          }, begin),
        )
      })
    }

    const resize = () => {
      const width = el.clientWidth
      if (width > 0) chart.resize({ width, height })
    }
    // 首帧布局可能还没稳定（移动端尤其明显）：下一帧按真实容器宽度重算一次
    const frame = window.requestAnimationFrame(resize)
    let observer: ResizeObserver | undefined
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(resize)
      observer.observe(el)
    }
    // 窗口尺寸变化始终兜底：ResizeObserver 在部分嵌入环境里不会触发
    window.addEventListener('resize', resize)

    return () => {
      window.cancelAnimationFrame(frame)
      timers.forEach((timer) => window.clearTimeout(timer))
      observer?.disconnect()
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [labels, series, height])

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height }}
      role="img"
      aria-label={ariaLabel}
    />
  )
}
