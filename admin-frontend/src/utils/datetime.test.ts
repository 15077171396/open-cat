import { describe, expect, it } from 'vitest'

import { formatDate, formatDateTime, formatRelative, formatTime, parseServerDate } from './datetime'

/**
 * 后端给的是 naive UTC 串（无时区后缀），JS 按本地时区解析会偏移 8 小时；
 * 本文件钉住「无偏移按 UTC 解析、展示用本地时区」的口径。
 * 断言与时区无关（UTC epoch 毫秒与本地 getter 反算），换机器不红。
 */

const NAIVE_UTC = '2026-10-12T03:00:00'
const INSTANT = Date.UTC(2026, 9, 12, 3, 0, 0)

describe('parseServerDate', () => {
  it('无偏移的串按 UTC 解释（这是本次修复的本体）', () => {
    expect(parseServerDate(NAIVE_UTC)?.getTime()).toBe(INSTANT)
  })

  it('显式 Z 与显式偏移按各自含义解析，结果与朴素串指向同一瞬间', () => {
    expect(parseServerDate('2026-10-12T03:00:00Z')?.getTime()).toBe(INSTANT)
    expect(parseServerDate('2026-10-12T11:00:00+08:00')?.getTime()).toBe(INSTANT)
  })

  it('空格分隔（"2026-10-12 03:00:00"）同样按 UTC 解析', () => {
    expect(parseServerDate('2026-10-12 03:00:00')?.getTime()).toBe(INSTANT)
  })

  it('空值与脏值返回 null（调用方回落原串，不显示 Invalid Date）', () => {
    expect(parseServerDate(null)).toBeNull()
    expect(parseServerDate('')).toBeNull()
    expect(parseServerDate('不是时间')).toBeNull()
  })
})

describe('格式化', () => {
  it('按本地时区渲染：结果必须等于同一瞬间的本地 getter', () => {
    const d = new Date(INSTANT)
    const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)
    const ymd = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    expect(formatDate(NAIVE_UTC)).toBe(ymd)
    expect(formatDateTime(NAIVE_UTC)).toBe(`${ymd} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`)
    expect(formatTime(NAIVE_UTC)).toBe(`${ymd} ${pad(d.getHours())}:${pad(d.getMinutes())}`)
  })

  it('修复的可观测证据：UTC 墙上时钟 03:00 不再被当作本地 03:00 显示', () => {
    const shown = formatDateTime(NAIVE_UTC)
    // 只有本机时区恰为 UTC 时才会与原串相同（CI 常见），此时跳过断言
    if (new Date(INSTANT).getTimezoneOffset() !== 0) {
      expect(shown).not.toBe('2026-10-12 03:00:00')
    }
  })

  it('无法解析时回落原串而不是 Invalid Date 字样', () => {
    expect(formatDateTime('2026-10-12T03:00:00-broken')).toContain('2026-10-12')
    expect(formatDateTime('2026-10-12T03:00:00-broken')).not.toMatch(/Invalid/)
    expect(formatDateTime(null)).toBe('-')
    expect(formatDate(undefined)).toBe('-')
  })

  it('相对时间：刚刚 / 分钟前分支可达（小程序同 bug 的回归护栏）', () => {
    const ago = (ms: number) => new Date(Date.now() - ms).toISOString()
    expect(formatRelative(ago(5_000))).toBe('刚刚')
    expect(formatRelative(ago(5 * 60_000))).toBe('5分钟前')
    expect(formatRelative(ago(3 * 3_600_000))).toBe('3小时前')
    // 超过 30 天回落成日期
    expect(formatRelative(ago(40 * 86_400_000))).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})
