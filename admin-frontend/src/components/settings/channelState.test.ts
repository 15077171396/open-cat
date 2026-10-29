import { describe, expect, it } from 'vitest'
import type { SettingDef } from './types'
import { effectiveValue, switchEnabled, switchPending } from './channelState'

const defs = [
  { key: 'phone_login_enabled', label: '手机号验证码登录', type: 'bool', group: 'auth_phone', group_label: '手机号验证码登录', default: '0' },
  { key: 'sms_provider', label: '短信服务商', type: 'select', group: 'auth_sms', group_label: '短信通道', default: 'mock' },
  { key: 'payment_mock_enabled', label: '启用 Mock 支付', type: 'bool', group: 'payment_general', group_label: '支付开关与订单', default: '1' },
] as SettingDef[]

describe('渠道状态与草稿联动（登录管理 / 支付与渠道共用）', () => {
  it('未显式配置时回退后端默认值', () => {
    expect(effectiveValue({}, 'sms_provider', defs)).toBe('mock')
    expect(effectiveValue({}, 'not_exist', defs)).toBe('')
  })

  it('开关改动立刻反映到渠道状态（不必等保存刷新）', () => {
    expect(switchEnabled({}, defs, 'phone_login_enabled')).toBe(false)
    expect(switchEnabled({ phone_login_enabled: '1' }, defs, 'phone_login_enabled')).toBe(true)
    // 默认值 "1" 的开关：未显式配置时也应是开启
    expect(switchEnabled({}, defs, 'payment_mock_enabled')).toBe(true)
  })

  it('无开关的渠道返回 undefined（由调用方回退后端状态）', () => {
    expect(switchEnabled({}, defs, undefined)).toBeUndefined()
    expect(switchPending({ phone_login_enabled: '1' }, {}, undefined)).toBe(false)
  })

  it('只有真正改动过才标记「保存后重新校验」', () => {
    expect(switchPending({}, {}, 'phone_login_enabled')).toBe(false)
    expect(switchPending({ phone_login_enabled: '1' }, {}, 'phone_login_enabled')).toBe(true)
    // 改回原值不算改动
    expect(
      switchPending({ phone_login_enabled: '1' }, { phone_login_enabled: '1' }, 'phone_login_enabled'),
    ).toBe(false)
  })
})
