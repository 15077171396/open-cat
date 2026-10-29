import type { ReactNode } from 'react'
import { Tag } from 'antd'

/** 语义色，对应设计规范 §六「状态与语义」与 tokens.css 的 --app-* */
export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

const TONE_COLORS: Record<StatusTone, { color: string; bg: string }> = {
  success: { color: 'var(--app-success)', bg: 'var(--app-success-bg)' },
  warning: { color: 'var(--app-warning)', bg: 'var(--app-warning-bg)' },
  danger: { color: 'var(--app-danger)', bg: 'var(--app-danger-bg)' },
  info: { color: 'var(--app-primary)', bg: 'var(--app-primary-weak)' },
  neutral: { color: 'var(--app-neutral)', bg: 'var(--app-neutral-bg)' },
}

export interface StatusTagProps {
  tone?: StatusTone
  children: ReactNode
}

/**
 * 状态标签。
 *
 * 为什么不用 `<Tag color="success">`：
 * - antd 的预设色只有 8 个档，`orange`(待审核) 与 `warning`(额度将尽) 在视觉上无法区分；
 * - 预设色的前景/背景对比度不受控，浅色底上小字常常不达标；
 * - 换主题时 antd 预设色不跟随品牌 token。
 *
 * 这里统一成 5 个语义档，颜色全部来自 `--app-*` 变量（与 theme.ts 同步）。
 *
 * 规则（设计规范 §六）：**危险色只用于破坏性状态**（删除/停用/强制下线），
 * 不要拿 `danger` 表示「已取消」这类中性状态——否则真正的危险在视觉上被稀释。
 */
export default function StatusTag({ tone = 'neutral', children }: StatusTagProps) {
  const c = TONE_COLORS[tone]
  return (
    <Tag
      style={{
        margin: 0,
        height: 22,
        lineHeight: '20px',
        paddingInline: 8,
        borderRadius: 'var(--app-radius-xs)',
        border: 'none',
        fontSize: 12,
        color: c.color,
        background: c.bg,
      }}
    >
      {children}
    </Tag>
  )
}

/** 布尔状态的常用映射：开/关、上架/下架、启用/禁用 */
export function boolTone(value: boolean, trueTone: StatusTone = 'success'): StatusTone {
  return value ? trueTone : 'neutral'
}
