import { describe, expect, it } from 'vitest'
import { RANGE_DAYS, buildAnalysisData, compactNumber } from './mockData'

/**
 * 演示数据的契约：
 * 1. 同一 seed / 范围可复现（截图、测试都依赖这点）；
 * 2. KPI 与趋势、来源与访客总数**自洽**，不能让卡片和图表互相打架；
 * 3. 换一批必须真的换数据，否则按钮是假的。
 */
describe('analysis mockData', () => {
  const fixedNow = new Date(2026, 9, 30, 12, 0, 0)

  it('同一 seed 与范围生成完全一致的数据', () => {
    const a = buildAnalysisData(42, '30d', fixedNow)
    const b = buildAnalysisData(42, '30d', fixedNow)
    expect(a.trend).toEqual(b.trend)
    expect(a.kpis).toEqual(b.kpis)
    expect(a.searchTerms).toEqual(b.searchTerms)
  })

  it('换 seed 会得到不同数据', () => {
    const a = buildAnalysisData(42, '30d', fixedNow)
    const b = buildAnalysisData(43, '30d', fixedNow)
    expect(a.trend).not.toEqual(b.trend)
    expect(a.pvTotal).not.toBe(b.pvTotal)
  })

  it('KPI 阅读总量与趋势图加总一致', () => {
    const data = buildAnalysisData(7, '90d', fixedNow)
    expect(data.trend).toHaveLength(RANGE_DAYS['90d'])
    expect(data.pvTotal).toBe(data.trend.reduce((sum, point) => sum + point.pv, 0))
    expect(data.uvTotal).toBe(data.trend.reduce((sum, point) => sum + point.uv, 0))
  })

  it('流量来源占比之和等于访客总数', () => {
    const data = buildAnalysisData(9, '30d', fixedNow)
    expect(data.sources.reduce((sum, item) => sum + item.value, 0)).toBe(data.uvTotal)
  })

  it('漏斗逐级收窄，且始终有四级', () => {
    const data = buildAnalysisData(11, '7d', fixedNow)
    expect(data.funnel).toHaveLength(4)
    for (let i = 1; i < data.funnel.length; i += 1) {
      expect(data.funnel[i].value).toBeLessThanOrEqual(data.funnel[i - 1].value)
    }
  })

  it('时间范围决定趋势点数，7/30/90 天都覆盖', () => {
    const short = buildAnalysisData(1, '7d', fixedNow)
    const month = buildAnalysisData(1, '30d', fixedNow)
    const quarter = buildAnalysisData(1, '90d', fixedNow)
    expect([short.trend.length, month.trend.length, quarter.trend.length]).toEqual([7, 30, 90])
  })

  it('compactNumber 在万级与千级都能读', () => {
    expect(compactNumber(8_420)).toBe('8,420')
    expect(compactNumber(12_345)).toBe('1.2 万')
    expect(compactNumber(1_234_567)).toBe('123 万')
  })
})
