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
import type { QuadrantPoint } from './mockData'

interface ScatterQuadrantProps {
  points: QuadrantPoint[]
  height?: number
  ariaLabel: string
}

/**
 * 内容四象限（ECharts scatter）。
 * 均值十字线用 markLine，四角文案用 graphic；hover 时 ECharts 放大当前点并显示 tooltip。
 */
export default function ScatterQuadrant({ points, height = 286, ariaLabel }: ScatterQuadrantProps) {
  const option = useMemo<EChartsOption>(() => {
    const categories = [...new Set(points.map((point) => point.category))]
    const avgViews = points.length
      ? points.reduce((sum, point) => sum + point.views, 0) / points.length
      : 0
    const avgCompletion = points.length
      ? points.reduce((sum, point) => sum + point.completion, 0) / points.length
      : 0

    return withAnimation({
      grid: { left: 46, right: 18, top: 18, bottom: 44 },
      legend: { ...LEGEND_BASE, bottom: 0, left: 'center', data: categories },
      tooltip: {
        ...TOOLTIP_BASE,
        trigger: 'item',
        formatter: (params: unknown) => {
          const data = (params as { data?: { title?: string; category?: string; views?: number; completion?: number } }).data
          if (!data) return ''
          return [
            data.title ?? '',
            `${data.category ?? ''} · 阅读 ${compactNumber(data.views ?? 0)}`,
            `完读率 ${data.completion ?? 0}%`,
          ].join('<br/>')
        },
      },
      xAxis: {
        type: 'value',
        name: '阅读量',
        nameLocation: 'end',
        nameGap: 6,
        nameTextStyle: { color: 'var(--app-text-3)', fontSize: 11 },
        axisLabel: {
          ...AXIS_LABEL,
          formatter: (value: number) => compactNumber(value),
        },
        splitLine: SPLIT_LINE,
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 100,
        name: '完读率',
        nameLocation: 'end',
        nameGap: 8,
        nameTextStyle: { color: 'var(--app-text-3)', fontSize: 11 },
        axisLabel: { ...AXIS_LABEL, formatter: '{value}%' },
        splitLine: SPLIT_LINE,
        axisLine: { show: false },
        axisTick: { show: false },
      },
      graphic: [
        { type: 'text', left: '12%', top: '10%', style: { text: '深度长尾', fill: 'var(--app-text-4)', fontSize: 11 } },
        { type: 'text', right: '5%', top: '10%', style: { text: '明星内容', fill: 'var(--app-text-4)', fontSize: 11 } },
        { type: 'text', left: '12%', bottom: '19%', style: { text: '待优化', fill: 'var(--app-text-4)', fontSize: 11 } },
        { type: 'text', right: '5%', bottom: '19%', style: { text: '流量泡沫', fill: 'var(--app-text-4)', fontSize: 11 } },
      ],
      series: categories.map((category, index) => ({
        name: category,
        type: 'scatter',
        symbolSize: 12,
        data: points
          .filter((point) => point.category === category)
          .map((point) => ({
            name: point.title,
            value: [point.views, point.completion],
            title: point.title,
            category,
            views: point.views,
            completion: point.completion,
          })),
        itemStyle: {
          color: points.find((point) => point.category === category)?.color,
          opacity: 0.85,
        },
        emphasis: {
          scale: 1.4,
          itemStyle: { opacity: 1, borderColor: 'var(--app-card)', borderWidth: 2 },
        },
        markLine:
          index === 0
            ? {
                silent: true,
                symbol: 'none',
                label: { show: false },
                lineStyle: { color: 'var(--app-text-4)', type: 'dashed', width: 1 },
                data: [{ xAxis: avgViews }, { yAxis: avgCompletion }],
              }
            : undefined,
        ...SERIES_ANIMATION,
      })),
    })
  }, [points])

  return <EChart option={option} height={height} ariaLabel={ariaLabel} />
}
