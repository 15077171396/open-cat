import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import EChart from './EChart'
import { TOOLTIP_BASE, withAnimation } from './chartTheme'
import { compactNumber } from './mockData'
import type { FunnelStage } from './mockData'

const STAGE_COLORS = ['#4263EB', '#6D5BFF', '#8B5CF6', '#A855F7'] as const

interface FunnelBarsProps {
  stages: FunnelStage[]
  height?: number
}

/** 转化漏斗（ECharts funnel）：hover 高亮当前层并显示数值与相对上一级的转化率。 */
export default function FunnelBars({ stages, height = 236 }: FunnelBarsProps) {
  const option = useMemo<EChartsOption>(
    () =>
      withAnimation({
        tooltip: {
          ...TOOLTIP_BASE,
          trigger: 'item',
          formatter: (params: unknown) => {
            const data = params as { dataIndex: number; name: string; value: number }
            const previous = stages[data.dataIndex - 1]
            const rate = previous && previous.value > 0
              ? `${((data.value / previous.value) * 100).toFixed(1)}%`
              : null
            return [
              data.name,
              `数量 ${compactNumber(data.value)}`,
              rate ? `上一级 → ${rate}` : null,
            ]
              .filter(Boolean)
              .join('<br/>')
          },
        },
        series: [
          {
            type: 'funnel',
            left: '6%',
            right: '6%',
            top: 16,
            bottom: 12,
            sort: 'descending',
            gap: 3,
            minSize: '16%',
            maxSize: '100%',
            label: {
              show: true,
              position: 'inside',
              color: '#fff',
              fontSize: 11,
              lineHeight: 17,
              formatter: (params: unknown) => {
                const data = params as { name: string; value: number }
                return `${data.name}\n${compactNumber(data.value)}`
              },
            },
            labelLine: { show: false },
            itemStyle: { borderWidth: 0 },
            emphasis: {
              label: { fontSize: 12 },
              itemStyle: { shadowBlur: 14, shadowColor: 'rgba(15, 23, 42, 0.2)' },
            },
            data: stages.map((stage, index) => ({
              name: stage.label,
              value: stage.value,
              itemStyle: { color: STAGE_COLORS[index % STAGE_COLORS.length] },
            })),
            animationDelay: 200,
            animationDuration: 1500,
            animationEasing: 'quarticOut',
          },
        ],
      }),
    [stages],
  )

  return <EChart option={option} height={height} ariaLabel="转化漏斗" />
}
