/**
 * 数据看板的纯逻辑层：图表几何计算 + 接口数据兜底。
 * 不含任何运行时 import（只有 import type），全部纯函数，可在 Node 下离线回归；
 * 需要「当前时间」的地方由调用方传入。
 */

import type {
  AiUsageData,
  AiUsageDayPoint,
  AiUsageModelRow,
  DashboardContentOverview,
  DashboardData,
  DashboardPending,
  DashboardTrendPoint,
  NormalizedAiUsage,
  NormalizedDashboard,
  ContentAttributionData,
  FunnelOverviewData,
  NormalizedAttribution,
  NormalizedFunnel,
  NormalizedSystemStatus,
  NormalizedScheduledJobs,
  ScheduledJobStatus,
  ScheduledJobsData,
  SystemStatusData,
} from '../../types'

// 一、图表几何

/** 折线图绘图区内边距（唯一事实来源：scaleTrend 与组件布局都引用它） */
export const CHART_PADDING = { top: 16, right: 16, bottom: 28, left: 44 } as const

/** 绘图区尺寸（从容器尺寸扣掉内边距，并夹紧为非负） */
export function plotSize(width: number, height: number): { plotW: number; plotH: number } {
  return {
    plotW: Math.max(0, width - CHART_PADDING.left - CHART_PADDING.right),
    plotH: Math.max(0, height - CHART_PADDING.top - CHART_PADDING.bottom),
  }
}

/** 面积图的基线 y（绘图区底边） */
export function baselineY(height: number): number {
  return height - CHART_PADDING.bottom
}

/**
 * 把坐标轴上界抬到能被 4 整除的整齐刻度上限。
 * Y 轴固定画 4 格，上界不是 4 的倍数会出现小数刻度（如 2.5），而指标全是整数计数。
 * 例：3 → 4；7 → 8；9 → 12；13 → 20；999 → 1000
 */
export function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 4
  if (value <= 4) return 4
  const exp = Math.pow(10, Math.floor(Math.log10(value)))
  const n = value / exp
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 4 ? 4 : n <= 5 ? 5 : n <= 8 ? 8 : 10
  return Math.max(4, Math.ceil((step * exp) / 4) * 4)
}

/** Y 轴刻度值（自下而上含 0 与上界）；segments 须能整除 niceMax（默认 4） */
export function niceTicks(max: number, segments = 4): number[] {
  const top = niceMax(max)
  const safeSegments = segments > 0 ? segments : 4
  return Array.from({ length: safeSegments + 1 }, (_, i) =>
    Math.round((top * i) / safeSegments),
  )
}

/** 数据点映射到绘图区后的坐标 */
interface ChartPoint {
  index: number
  /** 原始值 */
  value: number
  x: number
  y: number
}

/**
 * 把数值序列映射为绘图区坐标。
 * 空数组返回空（调用方渲染「暂无数据」）；单点居中避免除零；比值钳制到 [0,1]。
 */
export function scaleTrend(
  values: number[],
  width: number,
  height: number,
  max: number,
): ChartPoint[] {
  const { plotW, plotH } = plotSize(width, height)
  if (values.length === 0 || plotW <= 0 || plotH <= 0) return []
  const top = niceMax(max)
  const stepX = values.length === 1 ? 0 : plotW / (values.length - 1)
  return values.map((value, index) => {
    const ratio = Math.min(1, Math.max(0, value / top))
    return {
      index,
      value,
      x: CHART_PADDING.left + (values.length === 1 ? plotW / 2 : stepX * index),
      y: CHART_PADDING.top + plotH * (1 - ratio),
    }
  })
}

/** 保留两位小数，避免 SVG path 里出现超长浮点数 */
function round2(n: number): number {
  return Math.round(n * 100) / 100
}

/** 折线路径。刻意用直线段：平滑插值在「0 与 0 之间夹尖峰」时会过冲到基线以下，视觉上伪造负值 */
export function buildLinePath(points: ChartPoint[]): string {
  if (points.length === 0) return ''
  return points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${round2(p.x)},${round2(p.y)}`)
    .join(' ')
}

/** 面积路径（折线 + 沿基线闭合），用于渐变填充 */
export function buildAreaPath(points: ChartPoint[], baseY: number): string {
  if (points.length === 0) return ''
  const first = points[0]
  const last = points[points.length - 1]
  return `${buildLinePath(points)} L${round2(last.x)},${round2(baseY)} L${round2(first.x)},${round2(baseY)} Z`
}

/**
 * 悬浮命中测试：把鼠标在绘图区内的 x 坐标换算成最近的数据点下标。
 * 返回 null 表示无有效数据或坐标非法。
 */
export function hitTestIndex(
  offsetX: number,
  count: number,
  plotW: number,
): number | null {
  if (count <= 0 || !Number.isFinite(offsetX) || plotW <= 0) return null
  if (count === 1) return 0
  const ratio = Math.min(1, Math.max(0, offsetX / plotW))
  return Math.round(ratio * (count - 1))
}

/** 短日期：'2026-10-08' → '10-08'（X 轴用，年份在看板语境下是冗余信息） */
export function shortDate(iso: string): string {
  const parts = String(iso ?? '').split('-')
  return parts.length >= 3 ? `${parts[1]}-${parts[2]}` : String(iso ?? '')
}

/**
 * X 轴日期文案的抽样步长：相邻两个可见标签之间至少留 `minGap` 像素。
 *
 * 原先写的是「每点不足 44px 就隔一个显示」，只能处理 7~14 个点；30 天的窄图里
 * 隔一个仍会挤成一团（18px 一格塞 5 个字）。这里按实际密度算出步长，
 * 7 天宽图仍是 1（行为不变），30 天窄图自动变成 5~6 个标签。
 */
export function labelStepFor(count: number, plotW: number, minGap = 44): number {
  if (!Number.isFinite(count) || count <= 1) return 1
  if (!Number.isFinite(plotW) || plotW <= 0) return 1
  const perPoint = plotW / count
  if (perPoint >= minGap) return 1
  return Math.max(1, Math.ceil(minGap / perPoint))
}

// 二、环形图

export interface DonutInput {
  label: string
  value: number
  color: string
}

export interface DonutSlice {
  label: string
  value: number
  /** 占比 0~1 */
  ratio: number
  color: string
  /** 实线段长度（已扣掉分隔间隙），用于 stroke-dasharray */
  dash: number
  /** 该段起点偏移，用于 stroke-dashoffset */
  offset: number
}

interface DonutResult {
  slices: DonutSlice[]
  total: number
}

/**
 * 构造环形图切片：只统计 > 0 的项（0 值不占角度避免毛刺）；全部为 0 返回空切片；
 * 只剩 1 项时去掉分隔间隙（否则 100% 的环会被啃掉缺口）。
 */
export function buildDonutSlices(
  items: DonutInput[],
  circumference: number,
  gap = 0,
): DonutResult {
  const positive = (items ?? []).filter(
    (it) => it && Number.isFinite(it.value) && it.value > 0,
  )
  if (positive.length === 0 || !Number.isFinite(circumference) || circumference <= 0) {
    return { slices: [], total: 0 }
  }
  const total = positive.reduce((sum, it) => sum + it.value, 0)
  const effGap = positive.length === 1 ? 0 : Math.max(0, gap)
  let cursor = 0
  const slices = positive.map((it) => {
    const ratio = it.value / total
    const len = ratio * circumference
    const slice: DonutSlice = {
      label: it.label,
      value: it.value,
      ratio,
      color: it.color,
      dash: Math.max(0.5, len - effGap),
      offset: -cursor,
    }
    cursor += len
    return slice
  })
  return { slices, total }
}

// 三、接口数据兜底

/** 非负整数兜底：字段缺失、null、字符串数字、NaN、负数一律归 0 */
function toCount(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? Math.max(0, Math.trunc(n)) : 0
}

/** 比率兜底：保留一位小数（后端 conversion_rate 即一位小数） */
function toRate(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? Math.round(n * 10) / 10 : 0
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function toTrend(value: unknown): DashboardTrendPoint[] {
  if (!Array.isArray(value)) return []
  return value.filter(isRecord).map((p) => ({
    date: String(p.date ?? ''),
    count: toCount(p.count),
  }))
}

/**
 * 把接口返回归一化成组件可直接消费的完整结构。
 * 增强字段可能因 Redis 旧缓存或后端回滚而缺失，缺字段会让整页白屏；
 * 这里统一兜底成 0 / 空数组，使缺数据只表现为「某块显示 0」。
 */
export function normalizeDashboard(
  raw: DashboardData | null | undefined,
): NormalizedDashboard {
  const src: Partial<DashboardData> = raw ?? {}
  const overview: Partial<DashboardContentOverview> = src.content_overview ?? {}
  const pending: Partial<DashboardPending> = src.pending ?? {}
  const source = src.registration_source ?? { phone: 0, email: 0 }
  return {
    today_registrations: toCount(src.today_registrations),
    today_downloads: toCount(src.today_downloads),
    conversion_rate: toRate(src.conversion_rate),
    approved_count: toCount(src.approved_count),
    download_trend: toTrend(src.download_trend),
    total_users: toCount(src.total_users),
    total_tutorials: toCount(src.total_tutorials),
    registration_source: {
      phone: toCount(source.phone),
      email: toCount(source.email),
    },
    registration_trend: toTrend(src.registration_trend),
    active_users_7d: toCount(src.active_users_7d),
    today_tool_usage: toCount(src.today_tool_usage),
    content_overview: {
      tutorials: toCount(overview.tutorials),
    },
    pending: {
      auth_requests: toCount(pending.auth_requests),
      feedbacks: toCount(pending.feedbacks),
    },
  }
}

// 四、AI 用量

/** 金额兜底：未配置单价时后端返回 null，这里保持 null（不伪造成 0 元） */
function toCostOrNull(value: unknown): number | null {
  if (value === null || value === undefined) return null
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? Math.round(n * 10000) / 10000 : null
}

/** 可空计数：用于「未设上限」的剩余额度（后端以 null 表示不限制） */
function toNullableCount(value: unknown): number | null {
  if (value === null || value === undefined) return null
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? Math.max(0, Math.trunc(n)) : null
}

function toUsageDay(value: unknown): AiUsageDayPoint {
  const src = isRecord(value) ? value : {}
  const prompt = toCount(src.prompt_tokens)
  const completion = toCount(src.completion_tokens)
  return {
    date: String(src.date ?? ''),
    calls: toCount(src.calls),
    prompt_tokens: prompt,
    completion_tokens: completion,
    total_tokens: toCount(src.total_tokens) || prompt + completion,
    estimated_cost: toCostOrNull(src.estimated_cost),
  }
}

function toUsageTrend(value: unknown): AiUsageDayPoint[] {
  if (!Array.isArray(value)) return []
  return value.filter(isRecord).map(toUsageDay)
}

/** 按模型单列的单价表：形状不合法就丢弃该条（宁可少显示，也不能显示错的价） */
function toPriceTable(value: unknown): Record<string, { input: number; output: number }> {
  if (!isRecord(value)) return {}
  const out: Record<string, { input: number; output: number }> = {}
  for (const [model, price] of Object.entries(value)) {
    if (!model || !isRecord(price)) continue
    out[model] = {
      input: toCostOrNull(price.input) ?? 0,
      output: toCostOrNull(price.output) ?? 0,
    }
  }
  return out
}

/** 单个模型行：模型名必填，无名字的行无法归因直接丢弃 */
function toUsageModelRow(value: unknown): AiUsageModelRow | null {
  if (!isRecord(value)) return null
  const model = typeof value.model === 'string' ? value.model : ''
  if (!model) return null
  const prompt = toCount(value.prompt_tokens)
  const completion = toCount(value.completion_tokens)
  return {
    model,
    prompt_tokens: prompt,
    completion_tokens: completion,
    total_tokens: toCount(value.total_tokens) || prompt + completion,
    estimated_cost: toCostOrNull(value.estimated_cost),
  }
}

function toUsageModelRows(value: unknown): AiUsageModelRow[] {
  if (!Array.isArray(value)) return []
  return value.map(toUsageModelRow).filter((row): row is AiUsageModelRow => row !== null)
}

/** 「未归因」用量：只有总量大于 0 时才展示，否则会是「0 tokens / ¥0」的噪音行 */
function toUnattributed(value: unknown): AiUsageModelRow | null {
  const row = toUsageModelRow(value)
  if (!row) return null
  return (row.total_tokens ?? 0) > 0 ? row : null
}

/** 归一化 AI 用量数据：统一兜底成 0 / null，缺数据只表现为「显示 0」而非白屏 */
export function normalizeAiUsage(raw: AiUsageData | null | undefined): NormalizedAiUsage {
  const src: AiUsageData = raw ?? {}
  const today = src.today ?? {}
  const month = src.month ?? {}
  const limits = src.limits ?? {}
  const pricing = src.pricing ?? {}
  const todayDay = toUsageDay(today)
  const monthDay = toUsageDay(month)
  return {
    today: {
      ...todayDay,
      blocked: toCount(today.blocked),
      by_model: toUsageModelRows(today.by_model),
      unattributed: toUnattributed(today.unattributed),
      images: toCount(today.images),
      // 未配置单价时金额保持 null（不伪造成 0 元），面板据此显示「未配置单价」
      image_cost: toCostOrNull(today.image_cost),
      // 旧缓存 / 回滚可能不带该字段：退化为空数组，面板只 map 不判空
      images_by_model: Array.isArray(today.images_by_model) ? today.images_by_model : [],
    },
    month: {
      ...monthDay,
      by_model: toUsageModelRows(month.by_model),
      unattributed: toUnattributed(month.unattributed),
    },
    limits: {
      // 0 = 未启用该维度上限（与后端语义一致，不可用 0 当「没有额度」）
      daily: toCount(limits.daily),
      monthly: toCount(limits.monthly),
      remaining_today: toNullableCount(limits.remaining_today),
      remaining_month: toNullableCount(limits.remaining_month),
      // 插图限额同理：image_daily=0 表示不限，remaining_images=null 表示未设上限
      image_daily: toCount(limits.image_daily),
      remaining_images: toNullableCount(limits.remaining_images),
    },
    pricing: {
      input_per_mtok: toCostOrNull(pricing.input_per_mtok) ?? 0,
      output_per_mtok: toCostOrNull(pricing.output_per_mtok) ?? 0,
      configured: Boolean(pricing.configured),
      per_model: toPriceTable(pricing.per_model),
    },
    trend: toUsageTrend(src.trend),
  }
}

/** 大数字缩写（看板卡片空间有限）：1234 → 1.2k，1234567 → 1.2M */
export function shortNumber(value: number): string {
  const n = Number.isFinite(value) ? value : 0
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

/**
 * 金额展示：null（未配置单价）→ 「—」。
 *
 * 以前返回「未配置单价」，在卡片里会同一句话重复四遍（今日 / 本月 / 每个模型行）。
 * 现在金额位统一留「—」，配置状态由面板顶部的一句提示 + 「去配置」承担。
 * 小于 1 分的金额保留 4 位小数：DeepSeek 单价下几十次调用才几分钱，
 * 一律 `toFixed(2)` 会把真实花费显示成 ¥0.00（看起来像没记账）。
 */
export function formatCost(value: number | null): string {
  if (value === null) return '—'
  if (value === 0) return '¥0.00'
  if (value < 0.01) return `¥${value.toFixed(4)}`
  return `¥${value.toFixed(2)}`
}

/**
 * 插图费用展示：
 * - 用量接口失败时数值是占位 null，不能诬告「未配置单价」→ 「—」；
 * - 接口成功且 null = 确实没有配置图片单价 → 「未配置单价」；
 * - 数字 0 是配置了免费模型 → 「¥0.00」（不能当成未配置）。
 */
export function imageCostLabel(value: number | null, failed: boolean): string {
  if (failed) return '—'
  if (value === null) return '未配置单价'
  return formatCost(value)
}

// 五、AI 用量护栏

/**
 * 额度水位档位。
 * `unset` 是**独立档位而不是 0%**：后端用 limit=0 表示「该维度不限制」，
 * 与「有上限但一次都没用」在语义上完全不同，配色与文案都必须分开。
 */
export type QuotaTone = 'unset' | 'ok' | 'warn' | 'danger'

export interface QuotaState {
  used: number
  /** 0 = 未设上限 */
  limit: number
  /** 0~1，已夹紧；未设上限时为 0 */
  ratio: number
  /** 0~100 整数，未设上限时为 0 */
  percent: number
  /** 剩余次数；未设上限时为 null（不是 0） */
  remaining: number | null
  tone: QuotaTone
}

/** 水位告警阈值：≥80% 提醒、≥100% 触顶（与运维「留 20% 缓冲」的习惯一致） */
export const QUOTA_WARN_RATIO = 0.8

/** 计算某个维度（今日 / 本月）的额度水位 */
export function quotaState(used: number, limit: number): QuotaState {
  const safeUsed = Number.isFinite(used) ? Math.max(0, used) : 0
  const safeLimit = Number.isFinite(limit) ? Math.max(0, limit) : 0
  if (safeLimit <= 0) {
    return { used: safeUsed, limit: 0, ratio: 0, percent: 0, remaining: null, tone: 'unset' }
  }
  const ratio = safeUsed / safeLimit
  const tone: QuotaTone = ratio >= 1 ? 'danger' : ratio >= QUOTA_WARN_RATIO ? 'warn' : 'ok'
  return {
    used: safeUsed,
    limit: safeLimit,
    ratio: Math.min(1, ratio),
    percent: Math.round(Math.min(1, ratio) * 100),
    remaining: Math.max(0, safeLimit - safeUsed),
    tone,
  }
}

/**
 * 汇总多个维度的档位：取最严重的一个（danger > warn > ok > unset）。
 * 日上限触顶、月上限正常时，整体状态必须是「已达上限」。
 */
export function combineQuotaTone(tones: QuotaTone[]): QuotaTone {
  const order: QuotaTone[] = ['danger', 'warn', 'ok', 'unset']
  for (const tone of order) {
    if (tones.includes(tone)) return tone
  }
  return 'unset'
}

/** 按模型拆分的用量行（含「未归因」历史数据），用于面板里的占比条 */
export interface AiModelShare {
  key: string
  label: string
  tokens: number
  /** 未配置单价时为 null（显示「—」，不伪造 ¥0） */
  cost: number | null
  /** 占本月 tokens 的比例 0~1 */
  ratio: number
  /** 该模型配了「专属单价」 */
  priced: boolean
  /** 升级前写入、没有模型归属的历史用量 */
  unattributed: boolean
}

/**
 * 构造按模型明细：按 tokens 降序，未归因行固定排最后（它是历史残留，不该抢头名）。
 * 全部为 0 时返回空数组 —— 面板据此显示空态，而不是一排「0 tokens」。
 */
export function buildModelShares(
  rows: AiUsageModelRow[],
  unattributed: AiUsageModelRow | null,
  pricedModels: string[] = [],
): AiModelShare[] {
  const priced = new Set(pricedModels.filter((m) => typeof m === 'string' && m.length > 0))
  const models = (rows ?? [])
    .filter((row) => row && typeof row.model === 'string' && row.model.length > 0)
    .map((row) => ({
      key: row.model,
      label: row.model,
      tokens: toCount(row.total_tokens ?? (row.prompt_tokens ?? 0) + (row.completion_tokens ?? 0)),
      cost: toCostOrNull(row.estimated_cost ?? null),
      priced: priced.has(row.model),
      unattributed: false,
    }))
    .sort((a, b) => b.tokens - a.tokens)

  const list = [...models]
  if (unattributed && toCount(unattributed.total_tokens ?? 0) > 0) {
    list.push({
      key: '__unattributed__',
      label: '未归因',
      tokens: toCount(unattributed.total_tokens ?? 0),
      cost: toCostOrNull(unattributed.estimated_cost ?? null),
      priced: false,
      unattributed: true,
    })
  }

  const total = list.reduce((sum, row) => sum + row.tokens, 0)
  if (total <= 0) return []
  return list.map((row) => ({ ...row, ratio: row.tokens / total }))
}



// 五、系统状态（GET /admin/system-status）

/**
 * 把「可能缺字段」的运维状态接口结果归一化。
 *
 * 三条约定（都有单测钉住）：
 * - `ok: null` 表示**未知**，不是失败 —— 探针读不到版本/时延不能画成红色；
 * - 数组字段（jobs / warnings）缺省为空数组，组件只做 map 不做判空；
 * - 数字统一 `toCount`，避免后端返回 null 时出现 `NaN 小时`。
 */
export function normalizeSystemStatus(raw: SystemStatusData | null | undefined): NormalizedSystemStatus {
  const src = raw ?? {}
  const checks = src.checks ?? {}
  const mysql = checks.mysql ?? {}
  const redis = checks.redis ?? {}
  const schema = checks.schema ?? {}
  const logs = checks.logs ?? {}
  const backup = checks.backup ?? {}
  const alerts = checks.alerts ?? {}
  const quality = checks.data_quality ?? {}
  const env = src.env ?? {}

  const toBool = (value: unknown): boolean => value === true
  const toNullableBool = (value: unknown): boolean | null =>
    value === true ? true : value === false ? false : null
  const toNullableNumber = (value: unknown): number | null =>
    typeof value === 'number' && Number.isFinite(value) ? value : null

  return {
    ts: toCount(src.ts ?? 0),
    mysql: { ok: toNullableBool(mysql.ok), ms: toCount(mysql.ms ?? 0), error: String(mysql.error ?? '') },
    redis: { ok: toNullableBool(redis.ok), ms: toCount(redis.ms ?? 0), error: String(redis.error ?? '') },
    schema: {
      code_head: String(schema.code_head ?? ''),
      db_revision: String(schema.db_revision ?? ''),
      in_sync: toNullableBool(schema.in_sync),
    },
    logs: {
      dir: String(logs.dir ?? ''),
      visible: toBool(logs.visible),
      writable: toBool(logs.writable),
      last_write_age_minutes: toNullableNumber(logs.last_write_age_minutes),
    },
    backup: {
      dir: String(backup.dir ?? ''),
      visible: toBool(backup.visible),
      latest: String(backup.latest ?? ''),
      age_hours: toNullableNumber(backup.age_hours),
      stale: toNullableBool(backup.stale),
    },
    alerts: {
      sentry: toBool(alerts.sentry),
      watchdog_webhook: toBool(alerts.watchdog_webhook),
      baidu_push: toBool(alerts.baidu_push),
      indexnow: toBool(alerts.indexnow),
    },
    dataQuality: {
      // 空对象 = 巡检还没跑过或结果已过期（Redis TTL 3 天）。
      // 这种情况**不能**当「无异常」显示 —— 「没查过」和「查了没问题」在运维上是两件事
      available: Object.keys(quality).length > 0,
      issue_total: toCount(quality.issue_total ?? 0),
      issues: (quality.issues ?? []).map((item) => ({
        code: String(item.code ?? ''),
        level: String(item.level ?? 'warn'),
        count: toCount(item.count ?? 0),
        text: String(item.text ?? ''),
      })),
    },
    jobs: (src.jobs ?? []).map((job) => ({
      id: String(job.id ?? ''),
      label: String(job.label ?? job.id ?? ''),
      // 缺字段按「启用」：旧后端没有这个字段，显示成停用会让运维以为任务被人关了
      enabled: job.enabled !== false,
      last_run_at: toNullableNumber(job.last_run_at),
      ok: toNullableBool(job.ok),
      detail: String(job.detail ?? ''),
      stale: toBool(job.stale),
      stale_reason: String(job.stale_reason ?? ''),
    })),
    warnings: (src.warnings ?? []).map((item) => ({
      level: String(item.level ?? 'warn'),
      // 旧版本后端没有 category（升级窗口期），归到「其它」由前端兜底分类标签，
      // 不能丢这条提示 —— 丢了下线就少一条要处理的事
      category: String(item.category ?? 'other'),
      category_label: String(item.category_label ?? '其它'),
      text: String(item.text ?? ''),
    })),
    env: {
      app_env: String(env.app_env ?? ''),
      release: String(env.release ?? ''),
      monitoring_enabled: toBool(env.monitoring_enabled),
    },
  }
}

/**
 * 相对时间文案（运维页专用）：秒 → 「3 分钟前」。
 *
 * 刻意不引 dayjs/relativeTime（看板零新增依赖的约定）：这里只需要 4 档，
 * 且必须能离线单测 —— `now` 由调用方传入，函数本身不读时钟。
 */
export function formatAge(seconds: number | null | undefined, now?: number): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 60) return '刚刚'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  // 超过 30 天用绝对日期：运维看「1 年前」没有意义，直接给日期更好排查
  const ts = (now ?? Date.now() / 1000) - seconds
  return new Date(ts * 1000).toLocaleDateString('zh-CN')
}

/**
 * 绝对时刻（**固定北京时间**）：epoch 秒 → 「10-27 02:00」。
 *
 * 为什么不用 `toLocaleString()` 跟浏览器时区走：定时任务的周期是「北京时间 02:00」，
 * 管理员在海外/服务器时区不一致的浏览器里打开时，同比换算会让「下次执行」和
 * 「每日 02:00」对不上，看起来像任务调度错了。这里显式用 Asia/Shanghai，
 * 与后端 `scheduled_job_service.CST` 同一口径。
 */
export function formatCst(epoch: number | null | undefined): string {
  if (epoch === null || epoch === undefined || !Number.isFinite(epoch) || epoch <= 0) return '—'
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date(epoch * 1000))
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  // zh-CN 的 24 点会输出 "24"，统一成 "00"（否则「10-27 24:00」这种不存在的时刻会出现）
  const hour = get('hour') === '24' ? '00' : get('hour')
  return `${get('month')}-${get('day')} ${hour}:${get('minute')}`
}

// 六、转化漏斗与留存

/**
 * 归一化漏斗数据。
 *
 * 关键约定：`count === null` 表示**聚合失败（未知）**，必须原样保留成 null，
 * 不能兜底成 0 —— 「没人走到这一步」与「不知道有多少人」是两件完全不同的事，
 * 混成 0 会让运营误判「这一段彻底没人要」。
 */
export function normalizeFunnel(raw: FunnelOverviewData | null | undefined): NormalizedFunnel {
  const src = raw ?? {}
  const funnel = src.funnel ?? {}
  const retention = src.retention ?? {}

  const toNullableNumber = (value: unknown): number | null =>
    typeof value === 'number' && Number.isFinite(value) ? value : null
  const toRate = (value: unknown): number | null => {
    const n = toNullableNumber(value)
    if (n === null) return null
    return Math.min(1, Math.max(0, n))
  }

  return {
    days: toCount(funnel.days ?? 30),
    since: String(funnel.since ?? ''),
    stages: (funnel.stages ?? []).map((stage) => ({
      key: String(stage.key ?? ''),
      label: String(stage.label ?? stage.key ?? ''),
      count: toNullableNumber(stage.count),
      rateFromStart: toRate(stage.rate_from_start),
      rateFromPrev: toRate(stage.rate_from_prev),
    })),
    approvalRate: toRate(funnel.approval_rate),
    paidRate: toRate(funnel.paid_rate),
    retentionDays: toCount(retention.days ?? 14),
    retention: (retention.rows ?? []).map((row) => ({
      date: String(row.date ?? ''),
      cohort: toCount(row.cohort ?? 0),
      d1Active: toNullableNumber(row.d1_active),
      d7Active: toNullableNumber(row.d7_active),
      d1Rate: toRate(row.d1_rate),
      d7Rate: toRate(row.d7_rate),
    })),
  }
}

/**
 * 百分比展示：null → 「—」。
 *
 * 刻意不把 null 显示成 0%：分母为 0（还没有样本）与「转化率真的是零」在运营决策上
 * 是相反的两件事 —— 前者应继续观察，后者要立刻改产品。
 * 小于 0.1% 保留两位小数，避免「明明有人转化却显示 0%」。
 */
export function formatPercent(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return '—'
  const percent = rate * 100
  if (percent > 0 && percent < 0.1) return '<0.1%'
  if (percent >= 10) return `${percent.toFixed(1)}%`
  return `${percent.toFixed(1)}%`
}

// 七、内容归因

/**
 * 归一化内容归因数据。
 *
 * `available === false` 表示**还没采集**（迁移未执行 / 新部署），
 * 与「采集了但这几天没人浏览」是两件事：前者要去跑迁移，后者是正常现象。
 * `rate === null` 同理不能兜底成 0（viewers 为 0 时根本没有分母）。
 */
export function normalizeAttribution(raw: ContentAttributionData | null | undefined): NormalizedAttribution {
  const src = raw ?? {}
  const toNullableNumber = (value: unknown): number | null =>
    typeof value === 'number' && Number.isFinite(value) ? value : null
  return {
    days: toCount(src.days ?? 30),
    available: src.available !== false,
    windowDays: toCount(src.attribution_window_days ?? 7),
    totalViews: toCount(src.total_views ?? 0),
    totalViewers: toCount(src.total_viewers ?? 0),
    items: (src.items ?? []).map((item) => {
      const rate = toNullableNumber(item.rate)
      return {
        type: String(item.target_type ?? ''),
        id: toCount(item.target_id ?? 0),
        title: String(item.title ?? ''),
        viewers: toCount(item.viewers ?? 0),
        converted: toCount(item.converted ?? 0),
        rate: rate === null ? null : Math.min(1, Math.max(0, rate)),
      }
    }),
  }
}

// 八、定时任务（GET /admin/scheduled-jobs）

/** 任务依赖的配置项 → 中文名（后端只下发名字，展示文案在前端，免得环境变量名直接糊在页面上） */
const REQUIRE_LABELS: Record<string, string> = {
  BAIDU_PUSH_TOKEN: '百度推送 Token',
  INDEXNOW_KEY: 'IndexNow 密钥',
}

/** 后端状态可能因为版本差异缺席 —— 未知一律按「从未执行」展示，不伪造「正常」 */
function toJobStatus(value: unknown): ScheduledJobStatus {
  return value === 'ok' || value === 'failed' || value === 'stale' || value === 'never' || value === 'disabled'
    ? value
    : 'never'
}

const STATUS_FALLBACK_TEXT: Record<ScheduledJobStatus, string> = {
  ok: '正常',
  failed: '上次失败',
  stale: '未按时执行',
  never: '从未执行',
  disabled: '已停用',
}

/**
 * 归一化定时任务载荷。
 *
 * 两条约定：
 * - 分组、任务缺字段都兜底成空数组/占位文案，页面只 map 不判空；
 * - `status` 只认后端的四态，**不在前端二次推断**（stale / never 的口径差异由
 *   `scheduled_job_service` 单点决定，前端重算必然与提示文案对不上）。
 */
export function normalizeScheduledJobs(raw: ScheduledJobsData | null | undefined): NormalizedScheduledJobs {
  const src = raw ?? {}
  const summary = src.summary ?? {}
  const apply = src.apply ?? {}
  return {
    ts: toCount(src.ts ?? 0),
    timezone: String(src.timezone ?? 'UTC+08:00（北京时间）'),
    heartbeatTtlHours: toCount(src.heartbeat_ttl_hours ?? 72),
    summary: {
      total: toCount(summary.total ?? 0),
      ok: toCount(summary.ok ?? 0),
      failed: toCount(summary.failed ?? 0),
      stale: toCount(summary.stale ?? 0),
      never: toCount(summary.never ?? 0),
      disabled: toCount(summary.disabled ?? 0),
      attention: toCount(summary.attention ?? 0),
    },
    allSilent: src.all_silent === true,
    apply: {
      // 缺字段 = 后端还是「只读版」（不支持保存/执行），此时没有「待生效」可言；
      // alive 仍按 false 处理：它确实消费不了命令队列，不能让按钮看起来可点
      pending: apply.pending === true,
      appliedAt: toNullableEpoch(apply.applied_at),
      alive: apply.alive === true,
    },
    configsAvailable: src.configs_available !== false,
    runCooldownSeconds: toCount(src.run_cooldown_seconds ?? 10),
    groups: (src.groups ?? []).map((group) => ({
      key: String(group.key ?? ''),
      label: String(group.label ?? '未分组'),
      jobs: (group.jobs ?? []).map((job) => {
        const status = toJobStatus(job.status)
        const config = job.config ?? {}
        return {
          id: String(job.id ?? ''),
          label: String(job.label ?? job.id ?? '未命名任务'),
          desc: String(job.desc ?? ''),
          schedule: String(job.schedule ?? '—'),
          scheduleKind: String(job.schedule_kind ?? 'daily'),
          customized: job.customized === true,
          defaultSchedule: String(job.default_schedule ?? job.schedule ?? '—'),
          // 缺 enabled 时按「启用」：旧后端没有这个字段，显示成停用会误导
          enabled: job.enabled !== false && status !== 'disabled',
          config: {
            scheduleKind: String(config.schedule_kind ?? job.schedule_kind ?? 'daily'),
            hour: toCount(config.hour ?? 0),
            minute: toCount(config.minute ?? 0),
            intervalMinutes: toCount(config.interval_minutes ?? 1),
          },
          staleSeconds: toCount(job.stale_seconds ?? 0),
          note: String(job.note ?? ''),
          lastRunAt: toNullableEpoch(job.last_run_at),
          lastAgeSeconds:
            typeof job.last_age_seconds === 'number' && Number.isFinite(job.last_age_seconds)
              ? Math.max(0, job.last_age_seconds)
              : null,
          ok: job.ok === true ? true : job.ok === false ? false : null,
          detail: String(job.detail ?? ''),
          status,
          statusText: String(job.status_text ?? STATUS_FALLBACK_TEXT[status]),
          staleReason: String(job.stale_reason ?? ''),
          nextRunAt: toNullableEpoch(job.next_run_at),
          requires: (job.requires ?? []).map((item) => {
            const key = String(item.key ?? '')
            return { key, configured: item.configured === true, label: REQUIRE_LABELS[key] ?? key }
          }),
        }
      }),
    })),
  }
}

/** epoch 秒兜底：0/负数/非数字一律 null（0 会被 formatCst 画成 1970 年） */
function toNullableEpoch(value: unknown): number | null {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) && n > 0 ? n : null
}
