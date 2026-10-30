import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import EChart from './EChart'
import {
  AXIS_LABEL,
  SERIES_ANIMATION,
  SPLIT_LINE,
  TOOLTIP_BASE,
  withAnimation,
} from './chartTheme'
import { compactNumber } from './mockData'
import type { SearchTermRow } from './mockData'

interface SearchBarsProps {
  terms: SearchTermRow[]
  height?: number
}

/** 搜索热词（ECharts 横向柱状图）：hover 高亮当前词并显示量级与环比。 */
export default function SearchBars({ terms, height = 286 }: SearchBarsProps) {
  const option = useMemo<EChartsOption>(() => {
    // ECharts 类目轴从下往上排，反转后才能让第一名出现在顶部
    const ordered = [...terms].reverse()
    return withAnimation({
      grid: { left: 108, right: 52, top: 8, bottom: 8 },
      tooltip: {
        ...TOOLTIP_BASE,
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const list = params as { name: string; data: { value: number; delta: number } }[]
          const first = list?.[0]
          if (!first) return ''
          return `${first.name}<br/>搜索 ${compactNumber(first.data.value)} 次<br/>环比 ${
            first.data.delta >= 0 ? '+' : ''
          }${first.data.delta}%`
        },
      },
      xAxis: {
        type: 'value',
        axisLabel: {
          ...AXIS_LABEL,
          formatter: (value: number) => compactNumber(value),
        },
        splitLine: SPLIT_LINE,
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'category',
        data: ordered.map((item) => item.term),
        axisLabel: { ...AXIS_LABEL, width: 96, overflow: 'truncate' },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          data: ordered.map((item) => ({ value: item.count, delta: item.delta })),
          barWidth: '52%',
          itemStyle: { color: 'var(--app-primary)', borderRadius: [0, 4, 4, 0] },
          label: {
            show: true,
            position: 'right',
            color: 'var(--app-text-3)',
            fontSize: 11,
            formatter: (params: unknown) =>
              compactNumber((params as { value: number }).value),
          },
          emphasis: { focus: 'series' },
          ...SERIES_ANIMATION,
        },
      ],
    })
  }, [terms])

  return <EChart option={option} height={height} ariaLabel="搜索热词排行" />
}
