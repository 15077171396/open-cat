import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import EChart from './EChart'
import {
  AXIS_LABEL,
  LEGEND_BASE,
  SERIES_ANIMATION,
  SPLIT_LINE,
  TOOLTIP_BASE,
  withAnimation,
} from './chartTheme'
import { compactNumber } from './mockData'

export interface ColumnSeries {
  key: string
  name: string
  color: string
  data: number[]
}

interface ColumnChartProps {
  labels: string[]
  series: ColumnSeries[]
  height?: number
  stacked?: boolean
  ariaLabel: string
}

/**
 * 垂直柱状图（ECharts）。
 * 动画参数完全沿用参考项目：每列 200 + idx×50ms 延迟、1500 - idx×50ms 时长、quarticOut。
 * 悬浮时 ECharts 自动高亮当前柱并显示 axis tooltip。
 */
export default function ColumnChart({
  labels,
  series,
  height = 240,
  stacked = false,
  ariaLabel,
}: ColumnChartProps) {
  const option = useMemo<EChartsOption>(
    () =>
      withAnimation({
        grid: { left: 40, right: 12, top: series.length > 1 ? 34 : 12, bottom: 26 },
        legend:
          series.length > 1
            ? { ...LEGEND_BASE, top: 0, left: 0, data: series.map((item) => item.name) }
            : undefined,
        tooltip: {
          ...TOOLTIP_BASE,
          trigger: 'axis',
          axisPointer: { type: 'shadow' },
          valueFormatter: (value: unknown) => compactNumber(Number(value)),
        },
        xAxis: {
          type: 'category',
          data: labels,
          axisLabel: AXIS_LABEL,
          axisTick: { show: false },
          axisLine: { show: false },
        },
        yAxis: {
          type: 'value',
          axisLabel: {
            ...AXIS_LABEL,
            formatter: (value: number) => compactNumber(value),
          },
          splitLine: SPLIT_LINE,
          axisLine: { show: false },
          axisTick: { show: false },
        },
        series: series.map((item) => ({
          name: item.name,
          type: 'bar',
          data: item.data,
          stack: stacked ? 'total' : undefined,
          barWidth: stacked ? '54%' : labels.length > 12 ? '56%' : '46%',
          itemStyle: {
            color: item.color,
            borderRadius: stacked ? 0 : ([3, 3, 0, 0] as [number, number, number, number]),
          },
          emphasis: { focus: 'series' },
          ...SERIES_ANIMATION,
        })),
      }),
    [labels, series, stacked],
  )

  return <EChart option={option} height={height} ariaLabel={ariaLabel} />
}
