import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import Analysis from './Analysis'

/**
 * 分析页的骨架契约：
 * 1. 明确标注「演示数据」，不能让运营把它当真报表；
 * 2. 四张 KPI + 九个图表卡片都在；
 * 3. 切换时间范围 / 换一批会更新页面上的口径与种子。
 */
describe('Analysis 数据分析页', () => {
  const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
  const originalGetContext = HTMLCanvasElement.prototype.getContext

  beforeAll(() => {
    // jsdom 没有布局：给容器一个固定宽度，ECharts 才会真正渲染 SVG
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get() {
        return 800
      },
    })
    // ECharts 用 canvas 量文字宽度（即使最终用 SVG 渲染）；jsdom 没实现 getContext
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
      configurable: true,
      value: () => ({
        measureText: (text: string) => ({ width: text.length * 7 }),
      }),
    })
  })

  afterAll(() => {
    if (originalClientWidth) {
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
    }
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
      configurable: true,
      value: originalGetContext,
    })
  })

  it('渲染 KPI、图表卡片与演示数据标识', () => {
    render(<Analysis />)

    expect(screen.getByRole('heading', { name: '数据分析' })).toBeInTheDocument()
    expect(screen.getByText('演示数据')).toBeInTheDocument()

    for (const label of ['阅读总量', '新增注册', '内容完读率', '付费转化率']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }

    for (const title of [
      '阅读趋势',
      '流量来源',
      '内容表现',
      '内容四象限',
      '新老用户',
      '活跃时段',
      '转化漏斗',
      '内容质量雷达',
      '搜索热词',
    ]) {
      expect(screen.getByText(title)).toBeInTheDocument()
    }

    expect(screen.getByRole('img', { name: /阅读量与访客数趋势/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /24 小时阅读量分布/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /完读率四象限/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /质量五项指标/ })).toBeInTheDocument()
  })

  it('切换时间范围会同步统计口径与趋势点数', async () => {
    const { container } = render(<Analysis />)
    expect(screen.getByText(/统计口径：近 30 天/)).toBeInTheDocument()

    await userEvent.click(within(screen.getByRole('radiogroup')).getByText('近 7 天'))

    expect(screen.getByText(/统计口径：近 7 天/)).toBeInTheDocument()
    // ECharts 图表容器仍全部挂载（数据点几何由 mockData 用例单独守护）
    expect(container.querySelectorAll('.ana-card [role="img"]').length).toBeGreaterThanOrEqual(6)
  })

  it('「换一批」会更新种子与数据', async () => {
    render(<Analysis />)
    expect(screen.getByText(/#20261030/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /换一批/ }))

    expect(screen.getByText(/#20261031/)).toBeInTheDocument()
  })
})
