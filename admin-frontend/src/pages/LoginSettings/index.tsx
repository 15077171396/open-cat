import { useState } from 'react'
import { ExclamationCircleFilled, LoginOutlined, MailOutlined, SafetyOutlined } from '@ant-design/icons'
import SettingsEditor from '../../components/settings/SettingsEditor'
import FieldStack from '../../components/settings/FieldStack'
import { SettingsNav, SettingsPanel } from '../../components/settings/SettingsNav'
import ChannelCard, { type ChannelState } from '../../components/settings/ChannelCard'
import type { SettingsPayload } from '../../components/settings/types'
import { switchEnabled, switchPending } from '../../components/settings/channelState'

interface LoginSettingsPayload extends SettingsPayload {
  channels: ChannelState[]
}

const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  email: <MailOutlined />,
  captcha: <SafetyOutlined />,
}

/**
 * 渠道 → 开关键（邮箱密码登录没有开关，映射里不出现即回退后端状态）。
 * 登录管理后端只有邮箱密码（无开关）+ 登录图形验证码（login_captcha_enabled）两项。
 */
const CHANNEL_SWITCH_KEY: Record<string, string> = {
  captcha: 'login_captcha_enabled',
}

/** 虚拟导航项：渠道状态（不是设置分组，纯展示「保存后的实际效果」） */
const STATUS_KEY = '__status__'

/**
 * 登录管理：登录方式概览 + 登录人机验证开关。
 *
 * 登录渠道收敛后只剩两类：邮箱密码登录（基础方式，无开关）与登录图形验证码
 * （`login_captcha_enabled`，防跨 IP 慢速撞库）。页面仍保持「左导航（状态 + 配置组）
 * + 右单面板」的骨架：顶部是保存后的实际效果（渠道卡），配置组来自后端按
 * `surface=login` 下发的分组（当前只有 auth_captcha 一组）。
 */
export default function LoginSettingsPage() {
  // 默认停在「渠道状态」：进来先看登录方式清单，再决定要不要改验证码开关
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
        // 渠道收敛建没有「开着但凭据缺失」的坏状态；这个常驻条保留骨架，仅在异常数据时出现
        const brokenChannels = (payload.channels || []).filter((ch) => enabledOf(ch) && !ch.ready)

        const selectedGroup = groups.find((g) => g.key === activeKey)
        const showingStatus = !selectedGroup

        // 脏标记：状态页跟着「有任何改动」走，配置页跟着自己那一组走
        const dirtyKeys = new Set<string>([...(isDirty ? [STATUS_KEY] : []), ...dirtyGroups])

        return (
          <>
            {brokenChannels.length > 0 && (
              <div className="settings-banner settings-banner--warn">
                <ExclamationCircleFilled className="settings-banner__icon settings-banner__icon--warn" />
                <div>
                  <strong>{brokenChannels.length} 个渠道开着但不可用：</strong>
                  {brokenChannels.map((ch) => ch.label).join('、')}。
                  用户能看到入口，但点下去必然失败 —— 检查配置，或先把开关关掉。
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
                      这里是<strong>保存后</strong>的实际效果：开关改动会实时反映在卡片上。
                      前台登录页按 /auth/capabilities 的能力位自动显示对应入口。
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
