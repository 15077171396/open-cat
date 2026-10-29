/**
 * 渠道状态 ↔ 草稿的通用工具（登录管理 / 支付与渠道共用）。
 *
 * 两个页面顶部都有「渠道状态卡」，而状态卡的 `enabled` 必须跟着草稿实时变化 ——
 * 否则用户把开关关掉、卡片还写着「已开启」，只有保存并刷新后才变，读起来像没生效。
 * `ready`（凭据是否配齐）仍需后端算，那是渠道可用性的真正来源。
 */

import type { SettingDef } from './types'

/** 草稿里的有效值；空串表示「未显式配置」，回退后端给的默认值 */
export function effectiveValue(
  draft: Record<string, string>,
  key: string,
  defs: SettingDef[],
): string {
  const raw = draft[key]
  if (raw) return raw
  return defs.find((d) => d.key === key)?.default || ''
}

/**
 * 开关类设置当前是否开启。
 *
 * @param key 该渠道对应的开关键；`undefined` 表示这个渠道没有开关（如邮箱密码登录），
 *            返回 `undefined`，由调用方回退到后端给的 `enabled`。
 */
export function switchEnabled(
  draft: Record<string, string>,
  defs: SettingDef[],
  key?: string,
): boolean | undefined {
  if (!key) return undefined
  return effectiveValue(draft, key, defs) === '1'
}

/** 该渠道的开关是否有未保存改动（用于「保存后重新校验」提示） */
export function switchPending(
  draft: Record<string, string>,
  saved: Record<string, string>,
  key?: string,
): boolean {
  if (!key) return false
  return (draft[key] ?? '') !== (saved[key] ?? '')
}
