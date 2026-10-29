/** 允许的 URL 协议白名单 */
const ALLOWED_PROTOCOLS = ['http:', 'https:']

/** 校验 URL 仅允许 http/https 协议，防 javascript:、data: 等 XSS */
export function isSafeUrl(url: string): boolean {
  if (!url) return true // 空值允许（非必填字段）
  try {
    const parsed = new URL(url)
    return ALLOWED_PROTOCOLS.includes(parsed.protocol)
  } catch {
    return false // 解析失败视为不安全
  }
}
