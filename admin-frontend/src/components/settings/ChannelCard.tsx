import type { ReactNode } from 'react'
import { Tag } from 'antd'
import { CheckCircleFilled, ExclamationCircleFilled } from '@ant-design/icons'
import './settingsKit.css'

/** 后端下发的渠道摘要（登录管理 channels[] / 支付与渠道 channels[] 同构） */
export interface ChannelState {
  key: string
  label: string
  /** 开关是否打开（后端值；页面用草稿覆盖后传入 `enabled`） */
  enabled: boolean
  /** 凭据是否配齐（只有后端知道） */
  ready: boolean
  detail: string
  issues: string[]
}

interface ChannelCardProps {
  channel: ChannelState
  /** 覆盖 enabled：让卡片跟随草稿实时变化，不必等保存刷新 */
  enabled: boolean
  icon: ReactNode
  /** 该渠道开关有未保存改动 */
  pending?: boolean
  /** 危险态（如 Mock 支付在当前环境真实可用）：整卡走红色 */
  danger?: boolean
  /** 关闭时的说明（默认「前台不展示该入口」） */
  disabledHint?: string
}

/**
 * 渠道状态卡：三态语义必须在界面上区分开，否则「开关打开」会被误读成「能用」。
 *
 * - `enabled=false`：已关闭，前台不展示该入口；
 * - `enabled=true, ready=true`：可用；
 * - `enabled=true, ready=false`：**开着的坏状态** —— 用户能点、点了必失败。
 *   `issues` 说明缺什么，这是这些状态卡存在的首要理由。
 */
export default function ChannelCard({
  channel,
  enabled,
  icon,
  pending = false,
  danger = false,
  disabledHint = '前台不展示该入口',
}: ChannelCardProps) {
  const broken = enabled && !channel.ready
  const className = [
    'settings-channel',
    danger ? 'settings-channel--danger' : '',
    !danger && broken ? 'settings-channel--broken' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={className}>
      <div className="settings-channel__head">
        <span className="settings-channel__icon">{icon}</span>
        <span className="settings-channel__label">{channel.label}</span>
        <Tag color={enabled ? 'green' : 'default'} style={{ marginLeft: 'auto' }}>
          {enabled ? '已开启' : '已关闭'}
        </Tag>
      </div>

      <div className="settings-channel__state">
        {!enabled ? (
          <span className="settings-channel__state--muted">{disabledHint}</span>
        ) : channel.ready ? (
          <span className="settings-channel__state--ok">
            <CheckCircleFilled /> 可用
          </span>
        ) : (
          <span className="settings-channel__state--warn">
            <ExclamationCircleFilled /> 开着但不可用
          </span>
        )}
        {pending && <span className="settings-channel__pending">保存后重新校验</span>}
      </div>

      <div className="settings-channel__detail">{channel.detail}</div>

      {enabled &&
        channel.issues.map((issue) => (
          <div
            key={issue}
            className={`settings-channel__issue${danger ? ' settings-channel__issue--danger' : ''}`}
          >
            {issue}
          </div>
        ))}
    </div>
  )
}
