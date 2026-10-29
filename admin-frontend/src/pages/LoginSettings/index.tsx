import { useState } from 'react'
import { Collapse, Typography } from 'antd'
import {
  AppstoreOutlined,
  ExclamationCircleFilled,
  LoginOutlined,
  MailOutlined,
  MobileOutlined,
  WechatOutlined,
} from '@ant-design/icons'
import SettingsEditor from '../../components/settings/SettingsEditor'
import FieldStack from '../../components/settings/FieldStack'
import { SettingsNav, SettingsPanel } from '../../components/settings/SettingsNav'
import ChannelCard, { type ChannelState } from '../../components/settings/ChannelCard'
import type { SettingsPayload } from '../../components/settings/types'
import { effectiveValue, switchEnabled, switchPending } from '../../components/settings/channelState'
import './index.css'

interface LoginSettingsPayload extends SettingsPayload {
  channels: ChannelState[]
}

const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  email: <MailOutlined />,
  phone: <MobileOutlined />,
  wechat_web: <WechatOutlined />,
  wechat_mini: <AppstoreOutlined />,
}

/** 渠道 → 开关键（邮箱密码没有开关，映射里不出现即回退后端状态） */
const CHANNEL_SWITCH_KEY: Record<string, string> = {
  phone: 'phone_login_enabled',
  wechat_web: 'wechat_web_login_enabled',
  wechat_mini: 'wechat_mini_login_enabled',
}

/** 虚拟导航项：渠道状态（不是设置分组，纯展示「保存后的实际效果」） */
const STATUS_KEY = '__status__'

/** 短信参数按前缀归到「腾讯云 / 阿里云」两块；其余（服务商、频控）留在通用区 */
const TENCENT_PREFIX = 'sms_tencent_'
const ALIYUN_PREFIX = 'sms_aliyun_'

/**
 * 登录管理：登录方式开关 / 微信凭据 / 小程序凭据 / 短信通道。
 *
 * 2026-10-27 从「系统设置 → 登录与短信」抽出，动机有两个：
 * 1. 权限面 —— 开关登录方式、填短信凭据是运营/客服的日常动作，不该连带
 *    「改 COS 密钥 / 支付商户私钥」的 setting:update；
 * 2. 信息面 —— 原来的设置表单只能表达「开关是开还是关」，表达不了
 *    「开着但凭据没配齐 = 用户点了必然失败」，而那才是登录配置最常见的事故。
 *
 * 布局与「系统设置 / 支付与渠道」一致：左导航（概览 + 四个配置组）+ 右单面板。
 * 原来四张状态卡加四组配置竖着堆在一页，短信通道的 19 项在页面末端 ——
 * 配短信时看不到「手机号登录到底开没开」，而这正是最容易一起判断错的两件事。
 */
export default function LoginSettingsPage() {
  // 默认停在「渠道状态」：进来先看哪种登录方式真的能用，再决定去补哪一组
  const [activeKey, setActiveKey] = useState<string>(STATUS_KEY)

  return (
    <SettingsEditor<LoginSettingsPayload>
      endpoint="/admin/login-settings"
      title="登录管理"
      icon={<LoginOutlined />}
      savePerm="login_setting:update"
      // 登录配置不经公开设置缓存，保存即生效；集成配置缓存（60s）由后端主动失效
      saveSuccessText="登录配置已保存，立即生效（前台登录页刷新后可见）"
      loadErrorText="加载登录配置失败"
    >
      {({ groups, draft, saved, dirtyGroups, isDirty, setValue, payload }) => {
        const enabledOf = (ch: ChannelState) =>
          switchEnabled(draft, payload.definitions, CHANNEL_SWITCH_KEY[ch.key]) ?? ch.enabled
        const brokenChannels = (payload.channels || []).filter((ch) => enabledOf(ch) && !ch.ready)

        const selectedGroup = groups.find((g) => g.key === activeKey)
        const showingStatus = !selectedGroup

        // 脏标记：状态页跟着「有任何改动」走，配置页跟着自己那一组走
        const dirtyKeys = new Set<string>([...(isDirty ? [STATUS_KEY] : []), ...dirtyGroups])

        // 短信通道特殊处理：按服务商前缀拆成「通用 / 腾讯云 / 阿里云」三块
        const isSms = selectedGroup?.key === 'auth_sms'
        const provider = effectiveValue(draft, 'sms_provider', payload.definitions)
        const smsCommon = isSms
          ? selectedGroup.items.filter(
              (d) => !d.key.startsWith(TENCENT_PREFIX) && !d.key.startsWith(ALIYUN_PREFIX),
            )
          : []
        const smsTencent = isSms
          ? selectedGroup.items.filter((d) => d.key.startsWith(TENCENT_PREFIX))
          : []
        const smsAliyun = isSms ? selectedGroup.items.filter((d) => d.key.startsWith(ALIYUN_PREFIX)) : []

        return (
          <>
            {/* 「开着但不可用」常驻在导航之上：切到短信通道时也要能看见手机号登录是坏的 */}
            {brokenChannels.length > 0 && (
              <div className="settings-banner settings-banner--warn">
                <ExclamationCircleFilled className="settings-banner__icon settings-banner__icon--warn" />
                <div>
                  <strong>{brokenChannels.length} 个渠道开着但不可用：</strong>
                  {brokenChannels.map((ch) => ch.label).join('、')}。
                  用户能看到入口，但点下去必然失败 —— 补齐凭据，或先把开关关掉。
                </div>
              </div>
            )}
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
                ariaLabel="登录配置分类"
              />

              {showingStatus ? (
                <SettingsPanel
                  key={STATUS_KEY}
                  title="渠道状态"
                  description={
                    <>
                      这里是<strong>保存后</strong>的实际效果：开关改动会实时反映在卡片上（凭据是否配齐仍需保存后重新校验）。
                      前台只会展示「已开启且可用」的登录入口。
                    </>
                  }
                  dirty={isDirty}
                >
                  <div className="settings-channel-grid">
                    {(payload.channels || []).map((ch) => (
                      <ChannelCard
                        key={ch.key}
                        channel={ch}
                        enabled={enabledOf(ch)}
                        pending={switchPending(draft, saved, CHANNEL_SWITCH_KEY[ch.key])}
                        icon={CHANNEL_ICONS[ch.key] ?? <LoginOutlined />}
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
                  <FieldStack
                    items={isSms ? smsCommon : selectedGroup.items}
                    draft={draft}
                    setValue={setValue}
                  />

                  {isSms && (
                    <Collapse
                      // 切换服务商时重建：展开态自动落到当前选中的那一家
                      key={provider}
                      ghost
                      className="login-sms-collapse"
                      defaultActiveKey={[provider === 'aliyun' ? 'aliyun' : 'tencent']}
                      items={[
                        {
                          key: 'tencent',
                          label: (
                            <span>
                              腾讯云 SMS 参数
                              <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                                选择「腾讯云 SMS」时填写
                              </Typography.Text>
                            </span>
                          ),
                          children: <FieldStack items={smsTencent} draft={draft} setValue={setValue} />,
                        },
                        {
                          key: 'aliyun',
                          label: (
                            <span>
                              阿里云 SMS 参数
                              <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                                选择「阿里云 SMS」时填写
                              </Typography.Text>
                            </span>
                          ),
                          children: <FieldStack items={smsAliyun} draft={draft} setValue={setValue} />,
                        },
                      ]}
                    />
                  )}
                </SettingsPanel>
              )}
            </div>
          </>
        )
      }}
    </SettingsEditor>
  )
}
