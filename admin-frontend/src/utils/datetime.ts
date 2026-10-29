/**
 * 服务端时间解析与展示。
 * 后端 DATETIME 是 naive UTC（无时区后缀），JS 会把无偏移串按本地时区解析导致偏移 8 小时；
 * 统一口径：无偏移按 UTC 解析，展示用本地时区。
 */

/** 服务端时间串 → Date；无法解析返回 null */
export function parseServerDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const raw = String(value).trim().replace(' ', 'T')
  // 已带时区信息（Z 或 ±HH:mm）：交给 Date 自己解析
  if (/(Z|[+-]\d{2}:?\d{2})$/i.test(raw)) {
    const withOffset = new Date(raw)
    return Number.isNaN(withOffset.getTime()) ? null : withOffset
  }
  // 无偏移：后端给的是 UTC 朴素时间，补 Z 才是正确解读
  const asUtc = new Date(`${raw}Z`)
  return Number.isNaN(asUtc.getTime()) ? null : asUtc
}

const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)

/** 按本地时区渲染为 `YYYY-MM-DD HH:mm:ss` */
export function formatDateTime(value: string | null | undefined, fallback = '-'): string {
  const d = parseServerDate(value)
  if (!d) return value ? String(value).slice(0, 19).replace('T', ' ') : fallback
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 按本地时区渲染为 `YYYY-MM-DD HH:mm` */
export function formatTime(value: string | null | undefined, fallback = '-'): string {
  const d = parseServerDate(value)
  if (!d) return value ? String(value).slice(0, 16).replace('T', ' ') : fallback
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 按本地时区渲染为 `YYYY-MM-DD` */
export function formatDate(value: string | null | undefined, fallback = '-'): string {
  const d = parseServerDate(value)
  if (!d) return value ? String(value).slice(0, 10) : fallback
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 相对时间（刚 / N 分钟前 / N 小时前 / N 天前 / 日期） */
export function formatRelative(value: string | null | undefined): string {
  const d = parseServerDate(value)
  if (!d) return value ? String(value).slice(0, 16).replace('T', ' ') : '-'
  const diff = Date.now() - d.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}小时前`
  if (diff < 30 * 86_400_000) return `${Math.floor(diff / 86_400_000)}天前`
  return formatDate(d.toISOString())
}
