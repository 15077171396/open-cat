import { describe, expect, it } from 'vitest'
import {
  buildModelShares,
  combineQuotaTone,
  formatAge,
  formatCost,
  formatPercent,
  normalizeAiUsage,
  normalizeAttribution,
  normalizeFunnel,
  labelStepFor,
  normalizeSystemStatus,
  normalizeScheduledJobs,
  formatCst,
  imageCostLabel,
  quotaState,
} from './dashboardUtils'

/**
 * AI 用量护栏的纯逻辑回归。
 * 这些函数的输出直接决定「进度条颜色 / 剩余额度 / 金额是否显示」，
 * 算错不会报错，只会让运维看到错误的水位 —— 必须钉住。
 */
describe('quotaState', () => {
  it('limit=0 表示不限制：档位 unset、剩余为 null（不是 0）', () => {
    const q = quotaState(12, 0)
    expect(q.tone).toBe('unset')
    expect(q.remaining).toBeNull()
    expect(q.percent).toBe(0)
    // 未设上限也要保留真实用量，卡片上的数字不能因此归零
    expect(q.used).toBe(12)
  })

  it('未用量时为 ok 档位', () => {
    const q = quotaState(0, 100)
    expect(q.tone).toBe('ok')
    expect(q.percent).toBe(0)
    expect(q.remaining).toBe(100)
  })

  it('80% 起进入 warn 档位（边界值含 80%）', () => {
    expect(quotaState(79, 100).tone).toBe('ok')
    expect(quotaState(80, 100).tone).toBe('warn')
    expect(quotaState(80, 100).percent).toBe(80)
  })

  it('达到与超过上限都是 danger，比例夹紧到 100%', () => {
    const exact = quotaState(100, 100)
    expect(exact.tone).toBe('danger')
    expect(exact.percent).toBe(100)
    expect(exact.remaining).toBe(0)

    const over = quotaState(130, 100)
    expect(over.tone).toBe('danger')
    expect(over.percent).toBe(100)
    expect(over.ratio).toBe(1)
    // 超出部分不能让剩余额度变成负数（展示上会变成「剩余 -30」）
    expect(over.remaining).toBe(0)
  })

  it('非法输入归 0 而不是 NaN', () => {
    expect(quotaState(Number.NaN, 100).used).toBe(0)
    expect(quotaState(-5, 100).used).toBe(0)
    expect(quotaState(5, Number.NaN).tone).toBe('unset')
  })
})

describe('combineQuotaTone', () => {
  it('取最严重的档位：日上限触顶优先于月上限正常', () => {
    expect(combineQuotaTone(['ok', 'danger'])).toBe('danger')
    expect(combineQuotaTone(['warn', 'ok'])).toBe('warn')
  })

  it('两个维度都未设上限才算未启用', () => {
    expect(combineQuotaTone(['unset', 'unset'])).toBe('unset')
    // 只设了月上限时，护栏是生效的，不能显示「未启用」
    expect(combineQuotaTone(['unset', 'ok'])).toBe('ok')
  })
})

describe('buildModelShares', () => {
  it('按 tokens 降序，未归因固定排最后', () => {
    const rows = buildModelShares(
      [
        { model: 'small', total_tokens: 100, estimated_cost: 0.1 },
        { model: 'big', total_tokens: 900, estimated_cost: 0.9 },
      ],
      { model: '', total_tokens: 50, estimated_cost: null },
    )
    expect(rows.map((r) => r.label)).toEqual(['big', 'small', '未归因'])
    expect(rows[0].ratio).toBeCloseTo(900 / 1050)
    expect(rows[2].unattributed).toBe(true)
    expect(rows[2].cost).toBeNull()
  })

  it('标记配了专属单价的模型', () => {
    const rows = buildModelShares(
      [{ model: 'deepseek-chat', total_tokens: 10 }],
      null,
      ['deepseek-chat'],
    )
    expect(rows[0].priced).toBe(true)
  })

  it('全部为 0 时返回空数组（面板显示空态而不是一排 0 tokens）', () => {
    expect(buildModelShares([], null)).toEqual([])
    expect(buildModelShares([{ model: 'x', total_tokens: 0 }], null)).toEqual([])
  })

  it('缺 total_tokens 时用输入 + 输出兜底', () => {
    const rows = buildModelShares([{ model: 'x', prompt_tokens: 3, completion_tokens: 7 }], null)
    expect(rows[0].tokens).toBe(10)
  })

  it('丢弃没有模型名的脏行（无法归因的行不该出现在明细里）', () => {
    const rows = buildModelShares(
      [{ model: '', total_tokens: 5 }, { model: 'ok', total_tokens: 5 }],
      null,
    )
    expect(rows).toHaveLength(1)
    expect(rows[0].label).toBe('ok')
  })
})

describe('formatCost', () => {
  it('未配置单价显示「—」而不是 ¥0', () => {
    expect(formatCost(null)).toBe('—')
  })

  it('真实 0 元仍显示 ¥0.00（与未配置区分）', () => {
    expect(formatCost(0)).toBe('¥0.00')
  })

  it('不足一分钱保留 4 位小数，避免显示成 ¥0.00', () => {
    expect(formatCost(0.0042)).toBe('¥0.0042')
  })

  it('常规金额保留 2 位小数', () => {
    expect(formatCost(1.234)).toBe('¥1.23')
  })
})

describe('imageCostLabel', () => {
  it('未配置单价（null）显示「未配置单价」', () => {
    expect(imageCostLabel(null, false)).toBe('未配置单价')
  })

  it('配置了免费模型（数字 0）仍显示 ¥0.00，不能被当成未配置', () => {
    expect(imageCostLabel(0, false)).toBe('¥0.00')
  })

  it('用量接口失败时显示「—」，不诬告「未配置单价」', () => {
    expect(imageCostLabel(null, true)).toBe('—')
    expect(imageCostLabel(0, true)).toBe('—')
  })

  it('正常金额走 formatCost', () => {
    expect(imageCostLabel(0.6, false)).toBe('¥0.60')
  })
})

describe('normalizeAiUsage', () => {
  it('归一化插图指标', () => {
    const out = normalizeAiUsage({
      today: { images: 3, image_cost: 0.6, images_by_model: [{ model: 'wanx', images: 3 }] },
      limits: { image_daily: 30, remaining_images: 27 },
    } as never)
    expect(out.today.images).toBe(3)
    expect(out.today.image_cost).toBe(0.6)
    expect(out.limits.remaining_images).toBe(27)
  })

  it('插图字段缺失时兜底为 0 / null / 空数组，且不把未配置单价伪造成 0 元', () => {
    const out = normalizeAiUsage(null)
    expect(out.today.images).toBe(0)
    expect(out.today.image_cost).toBeNull()
    expect(out.today.images_by_model).toEqual([])
    expect(out.limits.image_daily).toBe(0)
    expect(out.limits.remaining_images).toBeNull()
  })
})

describe('labelStepFor', () => {
  it('7 天宽图不抽样（每个点都显示日期）', () => {
    expect(labelStepFor(7, 600)).toBe(1)
  })

  it('30 天窄图按密度抽样，标签不再重叠', () => {
    // 300px / 30 点 = 10px 一格 → 每 5 格显示一个（约 6 个标签）
    expect(labelStepFor(30, 300)).toBe(5)
    expect(labelStepFor(30, 900)).toBe(2)
  })

  it('宽度未知或单点时退化为 1，不产生 Infinity', () => {
    expect(labelStepFor(30, 0)).toBe(1)
    expect(labelStepFor(1, 300)).toBe(1)
    expect(labelStepFor(0, 300)).toBe(1)
  })
})

/**
 * 系统状态的纯逻辑回归。
 *
 * 重点不是「字段读对了」，而是三态语义：`ok: null` 是**未知**，绝不能渲染成失败；
 * 接口整块缺失时也要能渲染（运维页在最需要它的时候不能白屏）。
 */
describe('normalizeSystemStatus', () => {
  it('空响应也能渲染：数组为空、状态为未知', () => {
    const s = normalizeSystemStatus(null)
    expect(s.jobs).toEqual([])
    expect(s.warnings).toEqual([])
    expect(s.mysql.ok).toBeNull()
    expect(s.redis.ok).toBeNull()
    expect(s.schema.in_sync).toBeNull()
    expect(s.backup.visible).toBe(false)
    expect(s.dataQuality.issue_total).toBe(0)
  })

  it('ok=false 与 ok=null 必须区分（未知不能画成故障）', () => {
    const s = normalizeSystemStatus({ checks: { mysql: { ok: false, error: 'OperationalError' } } })
    expect(s.mysql.ok).toBe(false)
    expect(s.mysql.error).toBe('OperationalError')
    expect(s.redis.ok).toBeNull()
  })

  it('数字字段为 null 时不产生 NaN', () => {
    const s = normalizeSystemStatus({
      checks: { backup: { visible: true, age_hours: null, stale: null }, logs: { last_write_age_minutes: null } },
    })
    expect(s.backup.age_hours).toBeNull()
    expect(s.backup.stale).toBeNull()
    expect(s.logs.last_write_age_minutes).toBeNull()
    expect(Number.isNaN(s.mysql.ms)).toBe(false)
  })

  it('任务与告警列表原样保留（含 stale 与 detail）', () => {
    const s = normalizeSystemStatus({
      jobs: [{ id: 'a', label: '任务A', stale: true, stale_reason: '无执行记录', ok: null }],
      warnings: [{ level: 'error', category: 'dependency', category_label: '依赖与版本', text: 'MySQL 探测失败' }],
    })
    expect(s.jobs[0]).toMatchObject({ id: 'a', label: '任务A', stale: true, ok: null })
    expect(s.warnings[0]).toEqual({
      level: 'error',
      category: 'dependency',
      category_label: '依赖与版本',
      text: 'MySQL 探测失败',
    })
  })

  it('告警缺 category（旧版本后端）时归到 other，提示本身不能丢', () => {
    const s = normalizeSystemStatus({ warnings: [{ level: 'warn', text: '未配置 SENTRY_DSN' }] })
    expect(s.warnings[0]).toMatchObject({ category: 'other', text: '未配置 SENTRY_DSN' })
  })

  it('数据巡检结果缺失时 available=false（没查过不能显示「无异常」）', () => {
    expect(normalizeSystemStatus({ checks: {} }).dataQuality.available).toBe(false)
    expect(normalizeSystemStatus({ checks: { data_quality: { issue_total: 0 } } }).dataQuality.available).toBe(
      true,
    )
  })

  it('label 缺失时回退到 id，避免出现空白标签', () => {
    const s = normalizeSystemStatus({ jobs: [{ id: 'job_x' }] })
    expect(s.jobs[0].label).toBe('job_x')
  })
})

describe('formatAge', () => {
  it('按档位给出可读文案', () => {
    expect(formatAge(null)).toBe('—')
    expect(formatAge(undefined)).toBe('—')
    expect(formatAge(-1)).toBe('—')
    expect(formatAge(30)).toBe('刚刚')
    expect(formatAge(90)).toBe('1 分钟前')
    expect(formatAge(3 * 3600)).toBe('3 小时前')
    expect(formatAge(2 * 86400)).toBe('2 天前')
  })

  it('超过 30 天回落为绝对日期（由调用方传入 now，函数不读时钟）', () => {
    const now = Date.UTC(2026, 9, 27) / 1000
    const text = formatAge(60 * 86400, now)
    expect(text).not.toContain('天前')
    expect(text.length).toBeGreaterThan(4)
  })
})

/**
 * 转化漏斗的纯逻辑回归。
 *
 * 核心是「未知 ≠ 0」：聚合失败时 count 是 null，若兜底成 0，运营会看到
 * 「免费耗尽 0 人」并据此判断「没人愿意付费」—— 结论完全相反。
 */
describe('normalizeFunnel', () => {
  it('空响应也能渲染：阶段为空、比率为 null', () => {
    const f = normalizeFunnel(null)
    expect(f.stages).toEqual([])
    expect(f.retention).toEqual([])
    expect(f.approvalRate).toBeNull()
    expect(f.paidRate).toBeNull()
    expect(f.days).toBe(30)
  })

  it('count=null 原样保留（不能兜底成 0）', () => {
    const f = normalizeFunnel({
      funnel: { stages: [{ key: 'registered', label: '注册', count: null }] },
    })
    expect(f.stages[0].count).toBeNull()
  })

  it('count=0 与 null 必须区分', () => {
    const f = normalizeFunnel({ funnel: { stages: [{ key: 'consumed', count: 0 }] } })
    expect(f.stages[0].count).toBe(0)
  })

  it('比率越界会被夹到 0~1（后端异常值不至于画出超出 100% 的条）', () => {
    const f = normalizeFunnel({
      funnel: { stages: [{ key: 'a', count: 1, rate_from_prev: 1.7, rate_from_start: -0.2 }] },
    })
    expect(f.stages[0].rateFromPrev).toBe(1)
    expect(f.stages[0].rateFromStart).toBe(0)
  })

  it('留存行字段齐全，缺省回退', () => {
    const f = normalizeFunnel({ retention: { days: 7, rows: [{ date: '2026-10-01', cohort: 3 }] } })
    expect(f.retentionDays).toBe(7)
    expect(f.retention[0]).toMatchObject({ date: '2026-10-01', cohort: 3, d1Active: null, d7Rate: null })
  })
})

describe('formatPercent', () => {
  it('null 显示「—」而不是 0%', () => {
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(undefined)).toBe('—')
  })

  it('真的为 0 才显示 0.0%', () => {
    expect(formatPercent(0)).toBe('0.0%')
  })

  it('极小值不显示成 0%', () => {
    expect(formatPercent(0.0005)).toBe('<0.1%')
  })

  it('常规值保留一位小数', () => {
    expect(formatPercent(0.3333)).toBe('33.3%')
    expect(formatPercent(1)).toBe('100.0%')
  })
})

/**
 * 内容归因的纯逻辑回归。
 *
 * 两个「不能兜底」的点：
 * - `available=false`（还没采集）与「采集了但没数据」是相反结论；
 * - `rate=null`（没人看过）不能显示成 0%（那会被读成「看了都不转化」）。
 */
describe('normalizeAttribution', () => {
  it('空响应也能渲染', () => {
    const a = normalizeAttribution(null)
    expect(a.items).toEqual([])
    expect(a.available).toBe(true)
    expect(a.windowDays).toBe(7)
    expect(a.totalViews).toBe(0)
  })

  it('available=false 原样保留（尚未采集 ≠ 没有数据）', () => {
    expect(normalizeAttribution({ available: false }).available).toBe(false)
  })

  it('rate=null 与 rate=0 必须区分', () => {
    const a = normalizeAttribution({
      items: [
        { target_type: 'tutorial', target_id: 1, title: 'A', viewers: 0, converted: 0, rate: null },
        { target_type: 'tutorial', target_id: 2, title: 'B', viewers: 5, converted: 0, rate: 0 },
      ],
    })
    expect(a.items[0].rate).toBeNull()
    expect(a.items[1].rate).toBe(0)
  })

  it('比率越界会被夹到 0~1，标题缺省不影响渲染', () => {
    const a = normalizeAttribution({
      items: [{ target_type: 'resource', target_id: 9, rate: 1.8 }],
    })
    expect(a.items[0].rate).toBe(1)
    expect(a.items[0].title).toBe('')
    expect(a.items[0].type).toBe('resource')
  })
})

/**
 * 定时任务页的归一化。
 * 关键取舍：`status` 只认后端的四态、`available` 语义不能伪造成「正常」——
 * 这两点错了不会报错，只会让运维相信一个错的结论。
 */
describe('normalizeScheduledJobs', () => {
  it('空响应也能渲染（分组为空、汇总归零、不谎报全局静默）', () => {
    const s = normalizeScheduledJobs(null)
    expect(s.groups).toEqual([])
    expect(s.summary).toEqual({
      total: 0,
      ok: 0,
      failed: 0,
      stale: 0,
      never: 0,
      disabled: 0,
      attention: 0,
    })
    expect(s.allSilent).toBe(false)
    expect(s.heartbeatTtlHours).toBe(72)
    // 字段缺失 = 后端还是只读版：没有「待生效」可言，但确实消费不了命令（alive=false）
    expect(s.apply).toEqual({ pending: false, appliedAt: null, alive: false })
    expect(s.configsAvailable).toBe(true)
  })

  it('四态原样保留，未知状态降级为 never（不伪造「正常」）', () => {
    const s = normalizeScheduledJobs({
      groups: [
        {
          key: 'storage',
          label: '数据与存储',
          jobs: [
            { id: 'a', status: 'ok', status_text: '正常' },
            { id: 'b', status: 'failed', status_text: '上次失败' },
            { id: 'c', status: 'weird' },
          ],
        },
      ],
    })
    expect(s.groups[0].jobs.map((job) => job.status)).toEqual(['ok', 'failed', 'never'])
    expect(s.groups[0].jobs[2].statusText).toBe('从未执行')
  })

  it('依赖配置项映射成中文名与配置状态', () => {
    const s = normalizeScheduledJobs({
      groups: [
        {
          jobs: [{ id: 'x', requires: [{ key: 'BAIDU_PUSH_TOKEN', configured: false }] }],
        },
      ],
    })
    expect(s.groups[0].jobs[0].requires).toEqual([
      { key: 'BAIDU_PUSH_TOKEN', configured: false, label: '百度推送 Token' },
    ])
  })

  it('epoch 为 0 或缺失时归为 null（0 会被画成 1970 年）', () => {
    const s = normalizeScheduledJobs({ groups: [{ jobs: [{ id: 'x', last_run_at: 0 }] }] })
    expect(s.groups[0].jobs[0].lastRunAt).toBeNull()
    expect(s.groups[0].jobs[0].nextRunAt).toBeNull()
  })

  it('disabled 状态与 enabled 字段同时生效', () => {
    const s = normalizeScheduledJobs({
      groups: [{ jobs: [{ id: 'a', status: 'disabled', status_text: '已停用', enabled: false }] }],
    })
    expect(s.groups[0].jobs[0].status).toBe('disabled')
    expect(s.groups[0].jobs[0].enabled).toBe(false)
  })

  it('apply 标记与配置表可用性原样透传', () => {
    const s = normalizeScheduledJobs({
      apply: { pending: false, applied_at: 1793000000, alive: true },
      configs_available: false,
    })
    expect(s.apply).toEqual({ pending: false, appliedAt: 1793000000, alive: true })
    expect(s.configsAvailable).toBe(false)
  })

  it('all_silent 只认后端的 true', () => {
    expect(normalizeScheduledJobs({ all_silent: true }).allSilent).toBe(true)
    expect(normalizeScheduledJobs({}).allSilent).toBe(false)
  })
})

describe('formatCst', () => {
  it('固定按北京时间渲染（东八区，不受运行环境时区影响）', () => {
    // UTC 2026-10-26 18:00 == 北京时间 2026-10-27 02:00
    expect(formatCst(Date.UTC(2026, 9, 26, 18, 0) / 1000)).toBe('10-27 02:00')
  })

  it('空值与 0 不渲染成 1970 年', () => {
    expect(formatCst(null)).toBe('—')
    expect(formatCst(undefined)).toBe('—')
    expect(formatCst(0)).toBe('—')
  })
})
