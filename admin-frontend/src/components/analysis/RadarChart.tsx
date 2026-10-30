import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import EChart from './EChart'
import {
  LEGEND_BASE,
  SERIES_ANIMATION,
  TOOLTIP_BASE,
  withAnimation,
} from './chartTheme'
import type { RadarSeries } from './mockData'

interface RadarChartProps {
  indicators: string[]
  series: RadarSeries[]
  height?: number
  ariaLabel: string
}

/** 内容质量雷达（ECharts radar）：hover 数据点由 ECharts 显示 tooltip 并强调当前系列。 */
export default function RadarChart({ indicators, series, height = 286, ariaLabel }: RadarChartProps) {
  const option = useMemo<EChartsOption>(
    () =>
      withAnimation({
        legend: { ...LEGEND_BASE, bottom: 0, left: 'center', data: series.map((item) => item.name) },
        tooltip: { ...TOOLTIP_BASE, trigger: 'item' },
        radar: {
          indicator: indicators.map((name) => ({ name, max: 100 })),
          center: ['50%', '46%'],
          radius: '62%',
          splitNumber: 4,
          axisName: { color: 'var(--app-text-2)', fontSize: 11 },
          axisLine: { lineStyle: { color: 'var(--app-border-light)' } },
          splitLine: { lineStyle: { color: 'var(--app-border-light)' } },
          splitArea: { show: false },
        },
        series: [
          {
            type: 'radar',
            symbol: 'circle',
            symbolSize: 4,
            data: series.map((item) => ({
              name: item.name,
              value: item.values,
              itemStyle: { color: item.color },
              lineStyle: { color: item.color, width: 2 },
              areaStyle: { color: item.color, opacity: 0.14 },
            })),
            emphasis: { focus: 'series' },
            ...SERIES_ANIMATION,
          },
        ],
      }),
    [indicators, series],
  )

  return <EChart option={option} height={height} ariaLabel={ariaLabel} />
}
