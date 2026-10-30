import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import EChart from './EChart'
import { TOOLTIP_BASE, withAnimation } from './chartTheme'
import { compactNumber } from './mockData'
import type { SourceSlice } from './mockData'

interface SourcePieProps {
  items: SourceSlice[]
  height?: number
}

/**
 * 流量来源环形图（ECharts pie）。
 * 参考项目环形图用 `animationType: "expansion"`（从中心展开），
 * tooltip 固定 `名称: 数值 (百分比%)`；hover 时 ECharts 自动放大切片。
 */
export default function SourcePie({ items, height = 208 }: SourcePieProps) {
  const total = useMemo(() => items.reduce((sum, item) => sum + item.value, 0), [items])

  const option = useMemo<EChartsOption>(
    () =>
      withAnimation({
        color: items.map((item) => item.color),
        title: {
          text: compactNumber(total),
          subtext: '访客',
          left: '50%',
          top: '46%',
          textAlign: 'center',
          textVerticalAlign: 'middle',
          textStyle: { color: 'var(--app-text)', fontSize: 20, fontWeight: 600 },
          subtextStyle: { color: 'var(--app-text-2)', fontSize: 11 },
        },
        tooltip: {
          ...TOOLTIP_BASE,
          trigger: 'item',
          // 参考项目用一位小数（17.5%），ECharts {d} 默认给两位
          formatter: (params: unknown) => {
            const data = params as { name?: string; value?: number }
            const value = Number(data.value ?? 0)
            const percent = total > 0 ? (value / total) * 100 : 0
            return `${data.name ?? ''}: ${value} (${percent.toFixed(1)}%)`
          },
        },
        series: [
          {
            name: '流量来源',
            type: 'pie',
            radius: ['48%', '70%'],
            center: ['50%', '46%'],
            avoidLabelOverlap: false,
            itemStyle: {
              borderColor: 'var(--app-card)',
              borderWidth: 2,
              borderRadius: "var(--app-radius)",
            },
            label: { show: false },
            labelLine: { show: false },
            emphasis: {
              scale: true,
              scaleSize: 6,
              itemStyle: { shadowBlur: 16, shadowColor: 'rgba(15, 23, 42, 0.2)' },
            },
            data: items.map((item) => ({
              name: item.label,
              value: item.value,
              itemStyle: { color: item.color },
            })),
            animationType: 'expansion',
            animationDelay: 200,
            animationDuration: 1500,
            animationEasing: 'quarticOut',
          },
        ],
        // 图例由下方 DOM 渲染（带数值与百分比，比 ECharts 内置图例信息多）
      }),
    [items, total],
  )

  return (
    <div className="ana-source">
      <EChart option={option} height={height} ariaLabel="流量来源占比" />
      <ul className="dash-donut__legend ana-pie-legend">
        {items.map((item) => {
          const ratio = total > 0 ? item.value / total : 0
          return (
            <li key={item.label} className="dash-donut__legend-item">
              <span
                className="dash-donut__legend-dot"
                style={{ background: item.color }}
                aria-hidden="true"
              />
              <span className="dash-donut__legend-label">{item.label}</span>
              <span className="dash-donut__legend-value">{item.value}</span>
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
