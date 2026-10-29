import { useState } from 'react'
import {
  CreditCardOutlined,
  ExclamationCircleFilled,
  SafetyOutlined,
  WechatOutlined,
} from '@ant-design/icons'
import SettingsEditor from '../../components/settings/SettingsEditor'
import FieldStack from '../../components/settings/FieldStack'
import { SettingsNav, SettingsPanel } from '../../components/settings/SettingsNav'
import ChannelCard, { type ChannelState } from '../../components/settings/ChannelCard'
import type { SettingsPayload } from '../../components/settings/types'
import { switchEnabled, switchPending } from '../../components/settings/channelState'

interface PaymentWarning {
  level: 'danger' | 'warn' | 'info'
  text: string
}

interface PaymentSettingsPayload extends SettingsPayload {
  enabled: boolean
  env: string
  channels: ChannelState[]
  warnings: PaymentWarning[]
}

/** 渠道 → 开关键 */
const CHANNEL_SWITCH_KEY: Record<string, string> = {
  mock: 'payment_mock_enabled',
  wechat: 'wechat_pay_enabled',
  alipay: 'alipay_enabled',
}

const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  mock: <SafetyOutlined />,
  wechat: <WechatOutlined />,
  alipay: <CreditCardOutlined />,
}

/** 虚拟导航项：渠道状态（不是设置分组，纯展示「保存后的实际效果」） */
const STATUS_KEY = '__status__'

/** 警告级别 → 提示条的类名与图标（统一「白底 + 左侧色条」，不用整块彩色底） */
const WARNING_STYLE: Record<PaymentWarning['level'], { box: string; icon: string }> = {
  danger: { box: 'settings-banner--danger', icon: 'settings-banner__icon--danger' },
  warn: { box: 'settings-banner--warn', icon: 'settings-banner__icon--warn' },
  info: { box: 'settings-banner--info', icon: '' },
}

/**
 * 支付与渠道：支付开关 / 微信支付 / 支付宝。
 *
 * 2026-10-27 从「系统设置 → 支付与渠道」抽出（模块名沿用原 tab 名，避免运营找不到）；
 * 同日改为与「系统设置」一致的**左导航 + 右单面板**布局 —— 原来渠道状态卡与三组配置
 * 竖着堆在一页，{微信支付 8 项 + 支付宝 7 项} 滚到下面就看不见「现在到底能不能收钱」，
 * 而这两个信息恰恰要对照着看。
 *
 * 这一页额外承担「资金链路体检」（判定来自 `payments/factory.py`，与真实下单同一份代码）：
 * - **Mock 支付真实可用**时整卡标红（非生产环境下，用户点支付会直接到账）；
 * - 渠道开着但凭据没配齐时标「开着但不可用」（前台根本不展示，运营却以为已开）；
 * - 总开关开着却一个渠道都没有时，顶部直接说「用户点充值必然失败」。
 */
export default function PaymentSettingsPage() {
  // 默认停在「渠道状态」：进来先看能不能收钱，再决定去填哪一组
  const [activeKey, setActiveKey] = useState<string>(STATUS_KEY)

  return (
    <SettingsEditor<PaymentSettingsPayload>
      endpoint="/admin/payment-settings"
      title="支付与渠道"
      icon={<CreditCardOutlined />}
      savePerm="payment_setting:update"
      // 支付配置经 integration_settings_service 缓存（60s），保存后由后端主动失效
      saveSuccessText="支付配置已保存，立即生效（前台充值页刷新后可见）"
      loadErrorText="加载支付配置失败"
    >
      {({ groups, draft, saved, dirtyGroups, isDirty, setValue, payload }) => {
        const totalEnabled = switchEnabled(draft, payload.definitions, 'payment_enabled') ?? payload.enabled
        const enabledOf = (ch: ChannelState) =>
          switchEnabled(draft, payload.definitions, CHANNEL_SWITCH_KEY[ch.key]) ?? ch.enabled
        const usable = (payload.channels || []).filter((ch) => enabledOf(ch) && ch.ready)

        // 总开关本身也做成一张卡：它决定「充值入口是否出现」，比单个渠道更靠上
        const totalCard: ChannelState = {
          key: 'total',
          label: '在线支付（总开关）',
          enabled: totalEnabled,
          ready: usable.length > 0,
          detail: `当前环境：${payload.env}；可用渠道 ${usable.length} 个${
            usable.length ? `（${usable.map((c) => c.label).join('、')}）` : ''
          }`,
          issues:
            totalEnabled && usable.length === 0
              ? ['没有可用渠道：前台会展示充值入口，但用户点下去必然失败']
              : [],
        }

        const selectedGroup = groups.find((g) => g.key === activeKey)
        const showingStatus = !selectedGroup

        // 脏标记：状态页跟着「有任何改动」走，配置页跟着自己那一组走
        const dirtyKeys = new Set<string>([
          ...(isDirty ? [STATUS_KEY] : []),
          ...dirtyGroups,
        ])

        return (
          <>
            {/* 全站风险提示常驻在导航之上：切到微信支付时不希望「Mock 还开着」被藏起来 */}
            {(payload.warnings || []).map((w) => {
              const style = WARNING_STYLE[w.level] ?? WARNING_STYLE.info
              return (
                <div className={`settings-banner ${style.box}`} key={w.text}>
                  <ExclamationCircleFilled className={`settings-banner__icon ${style.icon}`} />
                  <div>{w.text}</div>
                </div>
              )
            })}

            {isDirty && (
              <div className="settings-banner settings-banner--info">
                有未保存的修改，点右上角「保存设置」后生效。
              </div>
            )}

            <div className="settings-layout">
              <SettingsNav
                categories={[
                  { key: 'status', label: '概览', items: [{ key: STATUS_KEY, label: '渠道状态' }] },
                  {
                    key: 'config',
                    label: '配置',
                    // 分组来自后端（顺序即后端声明顺序）；新增分组会自动出现在这里
                    items: groups.map((g) => ({ key: g.key, label: g.label })),
                  },
                ]}
                activeKey={showingStatus ? STATUS_KEY : activeKey}
                onSelect={setActiveKey}
                dirtyKeys={dirtyKeys}
                ariaLabel="支付配置分类"
              />

              {showingStatus ? (
                <SettingsPanel
                  key={STATUS_KEY}
                  title="渠道状态"
                  description={
                    <>
                      这里是<strong>保存后</strong>的实际效果：开关改动会实时反映在卡片上（凭据是否配齐仍需保存后重新校验）。
                      前台只展示「已开启且可用」的渠道。
                    </>
                  }
                  dirty={isDirty}
                >
                  <div className="settings-channel-grid">
                    <ChannelCard
                      channel={totalCard}
                      enabled={totalEnabled}
                      pending={switchPending(draft, saved, 'payment_enabled')}
                      icon={<CreditCardOutlined />}
                      disabledHint="前台不展示充值入口"
                    />
                    {(payload.channels || []).map((ch) => (
                      <ChannelCard
                        key={ch.key}
                        channel={ch}
                        enabled={enabledOf(ch)}
                        pending={switchPending(draft, saved, CHANNEL_SWITCH_KEY[ch.key])}
                        icon={CHANNEL_ICONS[ch.key] ?? <CreditCardOutlined />}
                        // Mock 真实可用 = 隐患，整卡走红色（后端已把它算进 warnings，这里同步视觉）
                        danger={ch.key === 'mock' && enabledOf(ch) && ch.ready}
                      />
                    ))}
                  </div>
                </SettingsPanel>
              ) : (
                <SettingsPanel
                  key={selectedGroup.key}
                  title={selectedGroup.label}
                  description={selectedGroup.description}
                  dirty={dirtyGroups.has(selectedGroup.key)}
                >
                  <FieldStack items={selectedGroup.items} draft={draft} setValue={setValue} />
                </SettingsPanel>
              )}
            </div>
          </>
        )
      }}
    </SettingsEditor>
  )
}
