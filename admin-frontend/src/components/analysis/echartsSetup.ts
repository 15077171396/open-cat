import * as echartsCore from 'echarts/core'
import { BarChart, FunnelChart, LineChart, PieChart, RadarChart, ScatterChart } from 'echarts/charts'
import {
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'

/**
 * 按需注册 ECharts 模块。
 *
 * 用 SVG 渲染器而不是 Canvas：坐标轴文字在任何缩放下都清晰，也便于在测试环境
 * （jsdom 没有 canvas）里真实渲染，不必把整页图表 mock 掉。
 * 只注册分析页用到的图表与组件，避免把 ECharts 全量塞进构建产物。
 */
echartsCore.use([
  LineChart,
  BarChart,
  PieChart,
  ScatterChart,
  RadarChart,
  FunnelChart,
  GridComponent,
  GraphicComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  MarkLineComponent,
  SVGRenderer,
])
