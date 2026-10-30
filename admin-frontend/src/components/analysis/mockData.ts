/**
 * 数据分析页的**演示数据**（纯前端生成，不请求后端、不写数据库）。
 *
 * 用固定种子 + mulberry32：同一 seed 每次得到同一组数字（测试可断言、截图可复现）；
 * 「换一批」只递增 seed，不参与任何业务写入。页面必须显著标注这是假数据，
 * 避免运营同学把它当成真实报表。
 */

export type AnalysisRange = '7d' | '30d' | '90d'

export const RANGE_DAYS: Record<AnalysisRange, number> = {
  '7d': 7,
  '30d': 30,
  '90d': 90,
}

export const RANGE_LABELS: Record<AnalysisRange, string> = {
  '7d': '近 7 天',
  '30d': '近 30 天',
  '90d': '近 90 天',
}

export interface AnalysisKpi {
  key: string
  label: string
  value: string
  /** 原始数值：供数字滚动动画使用（value 是最终展示文案，用于无障碍标签） */
  numeric: number
  format: 'compact' | 'percent'
  unit?: string
  delta: number
  /** 已格式化的涨跌文案（计数类用 %，比率类用 pp） */
  deltaLabel: string
  spark: number[]
  color: string
}

export interface TrendPoint {
  label: string
  /** 阅读量 */
  pv: number
  /** 访客数 */
  uv: number
}

export interface SourceSlice {
  label: string
  value: number
  color: string
}

export interface ContentRow {
  id: string
  title: string
  category: string
  views: number
  completion: number
  delta: number
}

export interface QuadrantPoint {
  id: string
  title: string
  views: number
  completion: number
  category: string
  color: string
}

export interface CohortPoint {
  label: string
  newUsers: number
  returning: number
}

export interface FunnelStage {
  key: string
  label: string
  value: number
  note: string
}

export interface RadarSeries {
  name: string
  color: string
  values: number[]
}

export interface SearchTermRow {
  term: string
  count: number
  delta: number
}

export interface AnalysisData {
  range: AnalysisRange
  days: number
  generatedAt: string
  seed: number
  kpis: AnalysisKpi[]
  trend: TrendPoint[]
  pvTotal: number
  uvTotal: number
  sources: SourceSlice[]
  contents: ContentRow[]
  quadrant: QuadrantPoint[]
  cohorts: CohortPoint[]
  hourly: number[]
  funnel: FunnelStage[]
  radar: { indicators: string[]; series: RadarSeries[] }
  searchTerms: SearchTermRow[]
}

const DAY_MS = 86_400_000

/** 内容站演示标题。刻意用「示例/入门」这类中性文案，不冒充真实文章 */
const DEMO_CONTENTS = [
  { title: 'ComfyUI 工作流：从节点到成图', category: 'AI 绘画' },
  { title: 'LoRA 模型训练：数据与参数', category: '模型训练' },
  { title: 'AI 绘画提示词工程', category: '提示词' },
  { title: 'Stable Diffusion 进阶技巧', category: 'AI 绘画' },
  { title: 'AI 视频生成入门', category: '视频生成' },
  { title: 'Midjourney 商业视觉实战', category: '商业应用' },
  { title: 'ControlNet 控制网详解', category: 'AI 绘画' },
  { title: '模型部署与推理加速', category: '工程化' },
] as const

const DEMO_TERMS = [
  'comfyui 工作流',
  'lora 训练',
  'ai 绘画提示词',
  'stable diffusion 下载',
  '视频生成 教程',
  'controlnet 使用',
] as const

const SOURCE_COLORS = ['#4263EB', '#0EA5A4', '#D97706', '#8B5CF6'] as const
const CONTENT_COLORS = ['#4263EB', '#0EA5A4', '#D97706', '#8B5CF6', '#EC4899', '#0EA5E9'] as const
const RADAR_COLORS = ['#4263EB', '#D97706'] as const

/** mulberry32：小而稳定的可复现随机数生成器 */
export function createRng(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4_294_967_296
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function between(rng: () => number, min: number, max: number): number {
  return min + rng() * (max - min)
}

function round(value: number): number {
  return Math.round(value)
}

/** 数字紧凑格式：1.2 万 / 8,420 */
export function compactNumber(value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(1)} 亿`
  if (Math.abs(value) >= 10_000) {
    const wan = value / 10_000
    return `${wan >= 100 ? wan.toFixed(0) : wan.toFixed(1)} 万`
  }
  return Math.round(value).toLocaleString('zh-CN')
}

function dayLabel(date: Date): string {
  return `${date.getMonth() + 1}/${date.getDate()}`
}

/** 把一组值压成 sparkline 点数（保持趋势形状，固定输出 count 个桶） */
function bucket(values: number[], count: number): number[] {
  if (values.length === 0) return []
  if (values.length <= count) return values.slice()
  const size = values.length / count
  return Array.from({ length: count }, (_, i) => {
    const start = Math.floor(i * size)
    const end = Math.max(start + 1, Math.floor((i + 1) * size))
    const slice = values.slice(start, end)
    return slice.reduce((sum, n) => sum + n, 0) / slice.length
  })
}

/** 围绕一个水平生成小幅随机游走，用于比率类 KPI 的迷你趋势 */
function randomWalk(rng: () => number, base: number, spread: number, count = 8): number[] {
  let value = base - spread / 2
  return Array.from({ length: count }, () => {
    value = clamp(value + (rng() - 0.45) * spread, base - spread, base + spread)
    return Math.round(value * 10) / 10
  })
}

function signed(value: number, digits = 1): string {
  const fixed = Math.abs(value).toFixed(digits)
  return `${value >= 0 ? '+' : '-'}${fixed}`
}

/** 生成一整页演示数据。`now` 仅测试注入用，页面默认取当前时间。 */
export function buildAnalysisData(
  seed: number,
  range: AnalysisRange,
  now: Date = new Date(),
): AnalysisData {
  const rng = createRng(seed)
  const days = RANGE_DAYS[range]
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())

  // ---- 阅读趋势：工作日高、周末低，叠加缓慢增长与噪声 ----
  const trend: TrendPoint[] = []
  const level = between(rng, 1900, 2800)
  for (let i = 0; i < days; i += 1) {
    const date = new Date(today.getTime() - (days - 1 - i) * DAY_MS)
    const dow = date.getDay()
    const weekend = dow === 0 || dow === 6
    const season = Math.sin((i / Math.max(1, days)) * Math.PI * 4) * 0.07
    const growth = 1 + (i / Math.max(1, days)) * 0.22
    const noise = between(rng, -0.14, 0.14)
    const pv = Math.max(120, round(level * growth * (weekend ? 0.76 : 1.06) * (1 + season + noise)))
    const uv = Math.max(60, round(pv * between(rng, 0.44, 0.56)))
    trend.push({ label: dayLabel(date), pv, uv })
  }
  const pvTotal = trend.reduce((sum, p) => sum + p.pv, 0)
  const uvTotal = trend.reduce((sum, p) => sum + p.uv, 0)

  // ---- KPI：与趋势同源，避免「卡片总数」和「图里加总」对不上 ----
  const growthRate = between(rng, 0.08, 0.26)
  const prevReads = pvTotal / (1 + growthRate)
  const newUserTotal = round(uvTotal * between(rng, 0.12, 0.17))
  const prevNewUsers = newUserTotal / (1 + between(rng, 0.02, 0.2))
  const completion = between(rng, 48, 62)
  const completionDelta = between(rng, -2.4, 3.2)
  const conversion = between(rng, 3.2, 5.6)
  const conversionDelta = between(rng, -0.6, 0.7)

  const kpis: AnalysisKpi[] = [
    {
      key: 'reads',
      label: '阅读总量',
      value: compactNumber(pvTotal),
      numeric: pvTotal,
      format: 'compact',
      delta: ((pvTotal - prevReads) / prevReads) * 100,
      deltaLabel: `${signed(((pvTotal - prevReads) / prevReads) * 100)}%`,
      spark: bucket(trend.map((p) => p.pv), 8),
      color: '#34D399',
    },
    {
      key: 'new_users',
      label: '新增注册',
      value: compactNumber(newUserTotal),
      numeric: newUserTotal,
      format: 'compact',
      delta: ((newUserTotal - prevNewUsers) / prevNewUsers) * 100,
      deltaLabel: `${signed(((newUserTotal - prevNewUsers) / prevNewUsers) * 100)}%`,
      spark: bucket(trend.map((p) => p.uv), 8),
      color: '#38BDF8',
    },
    {
      key: 'completion',
      label: '内容完读率',
      value: `${completion.toFixed(1)}%`,
      numeric: completion,
      format: 'percent',
      delta: completionDelta,
      deltaLabel: `${signed(completionDelta)}pp`,
      spark: randomWalk(rng, completion, 4),
      color: '#FBBF24',
    },
    {
      key: 'conversion',
      label: '付费转化率',
      value: `${conversion.toFixed(1)}%`,
      numeric: conversion,
      format: 'percent',
      delta: conversionDelta,
      deltaLabel: `${signed(conversionDelta, 1)}pp`,
      spark: randomWalk(rng, conversion, 0.9),
      color: '#A78BFA',
    },
  ]

  // ---- 流量来源：权重加噪后归一，保证四段之和就是访客总数 ----
  const rawWeights = [0.38, 0.32, 0.2, 0.1].map((w) => w * between(rng, 0.9, 1.1))
  const weightSum = rawWeights.reduce((a, b) => a + b, 0)
  const sourceLabels = ['直接访问', '搜索引擎', '微信 / 小程序', '外部链接']
  const sources: SourceSlice[] = []
  let allocated = 0
  rawWeights.forEach((weight, index) => {
    const value =
      index === rawWeights.length - 1
        ? Math.max(0, uvTotal - allocated)
        : round((uvTotal * weight) / weightSum)
    allocated += value
    sources.push({ label: sourceLabels[index], value, color: SOURCE_COLORS[index] })
  })

  // ---- 内容排行与四象限共用同一批标题 ----
  const scale = days / 30
  const contents: ContentRow[] = DEMO_CONTENTS.slice(0, 6).map((item, index) => {
    const views = round((42_000 - index * 5_600) * scale * between(rng, 0.82, 1.18))
    return {
      id: `c-${index}`,
      title: item.title,
      category: item.category,
      views,
      completion: round(between(rng, 42, 88)),
      delta: round(between(rng, -18, 42)),
    }
  })

  // 同一分类用同一颜色，否则图例里的色点会和不特定某个点对不上
  const categoryColors = new Map<string, string>()
  for (const item of DEMO_CONTENTS) {
    if (!categoryColors.has(item.category)) {
      categoryColors.set(item.category, CONTENT_COLORS[categoryColors.size % CONTENT_COLORS.length])
    }
  }
  const quadrant: QuadrantPoint[] = DEMO_CONTENTS.map((item, index) => ({
    id: `q-${index}`,
    title: item.title,
    category: item.category,
    views: round((38_000 - index * 4_100) * scale * between(rng, 0.7, 1.3)),
    completion: round(between(rng, 32, 92)),
    color: categoryColors.get(item.category) ?? CONTENT_COLORS[0],
  }))

  // ---- 新老用户：固定近 7 天，与全局时间范围解耦（参考页也是固定一周） ----
  const cohorts: CohortPoint[] = Array.from({ length: 7 }, (_, i) => {
    const date = new Date(today.getTime() - (6 - i) * DAY_MS)
    return {
      label: dayLabel(date),
      newUsers: round(between(rng, 18, 62)),
      returning: round(between(rng, 10, 46)),
    }
  })

  // ---- 24 小时活跃曲线：上午与晚间双峰 ----
  const hourCurve = [3, 2, 1, 1, 2, 5, 12, 26, 42, 58, 66, 72, 64, 56, 50, 54, 62, 70, 78, 84, 76, 64, 44, 20]
  const hourly = hourCurve.map((base) => Math.max(1, round(base * between(rng, 0.78, 1.22))))

  // ---- 漏斗：从曝光到会员，逐级收窄 ----
  const funnel: FunnelStage[] = [
    { key: 'exposure', label: '内容曝光', value: round(pvTotal * between(rng, 1.12, 1.24)), note: '列表 / 搜索 / 推荐' },
    { key: 'read', label: '开始阅读', value: pvTotal, note: '打开教程详情' },
    { key: 'engage', label: '登录或收藏', value: round(uvTotal * between(rng, 0.34, 0.42)), note: '产生深度行为' },
    { key: 'member', label: '开通会员', value: round(uvTotal * between(rng, 0.04, 0.06)), note: '完成付费转化' },
  ]

  // ---- 内容质量雷达：本月 vs 上月 ----
  const indicators = ['完读率', '互动率', '收藏率', '分享率', '付费转化']
  const radar: { indicators: string[]; series: RadarSeries[] } = {
    indicators,
    series: [
      {
        name: '本月',
        color: RADAR_COLORS[0],
        values: indicators.map(() => round(between(rng, 62, 92))),
      },
      {
        name: '上月',
        color: RADAR_COLORS[1],
        values: indicators.map(() => round(between(rng, 48, 78))),
      },
    ],
  }

  // ---- 搜索热词 ----
  const searchTerms: SearchTermRow[] = DEMO_TERMS.map((term) => ({
    term,
    count: round(between(rng, 260, 2400)),
    delta: round(between(rng, -22, 38)),
  })).sort((a, b) => b.count - a.count)

  return {
    range,
    days,
    generatedAt: new Date().toLocaleString('zh-CN', { hour12: false }),
    seed,
    kpis,
    trend,
    pvTotal,
    uvTotal,
    sources,
    contents,
    quadrant,
    cohorts,
    hourly,
    funnel,
    radar,
    searchTerms,
  }
}
