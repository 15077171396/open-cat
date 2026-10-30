import { useState } from 'react'
import { SettingOutlined } from '@ant-design/icons'
import SettingsEditor from '../../components/settings/SettingsEditor'
import { SettingField } from '../../components/settings/SettingField'
import { SettingsNav, SettingsPanel } from '../../components/settings/SettingsNav'
import { buildNav, firstGroupKey } from './settingsNav'

/**
 * 系统设置：通用设置项（数据与保存逻辑在 `SettingsEditor`）。
 *
 * 分组由后端按 `surface="settings"` 下发 —— 登录 / 法律文档 / 支付 / 邮件 / AI 成本护栏
 * 都已抽成独立页面，这里的导航里不会再出现它们（同一批开关出现在两个页面、各自保存
 * 互相覆盖，是这类「抽模块」重构最容易留下的尾巴）。
 *
 * 布局：**左侧分类导航 + 右侧单组表单**。14 个分组平铺成一行 tab 时，
 * 找一项设置要先扫一遍整行；分类竖排后每屏最多 6 项，且能看出「哪一栏里有未保存的修改」。
 * 导航/面板组件在 `components/settings/SettingsNav.tsx`，与「支付与渠道」共用。
 */
export default function SettingsPage() {
  const [activeKey, setActiveKey] = useState('')

  return (
    <SettingsEditor
      endpoint="/admin/settings"
      title="系统设置"
      icon={<SettingOutlined />}
      savePerm="setting:update"
      saveSuccessText="设置已保存，前台刷新后生效（缓存最多延迟 5 分钟）"
      loadErrorText="加载系统设置失败"
    >
      {({ groups, draft, dirtyGroups, setValue }) => {
        const nav = buildNav(groups)
        // 选中项失效（例如分组被抽走）时回落到第一个可选项
        const selected =
          groups.find((g) => g.key === activeKey) ??
          groups.find((g) => g.key === firstGroupKey(nav))

        return (
          <div className="settings-layout">
            <SettingsNav
              categories={nav.map((category) => ({
                key: category.key,
                label: category.label,
                items: category.groups.map((g) => ({ key: g.key, label: g.label })),
              }))}
              activeKey={selected?.key ?? ''}
              onSelect={setActiveKey}
              dirtyKeys={dirtyGroups}
              ariaLabel="系统设置分类"
            />

            <SettingsPanel
              key={selected?.key}
              title={selected?.label ?? ''}
              description={selected?.description}
              dirty={!!selected && dirtyGroups.has(selected.key)}
            >
              {selected ? (
                selected.items.map((d) => (
                  <SettingField
                    key={d.key}
                    def={d}
                    value={draft[d.key] ?? ''}
                    onChange={(v) => setValue(d.key, v)}
                  />
                ))
              ) : (
                <span style={{ color: 'var(--app-text-3)' }}>没有可显示的设置项。</span>
              )}
            </SettingsPanel>
          </div>
        )
      }}
    </SettingsEditor>
  )
}
