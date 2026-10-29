import type { ReactNode } from 'react'
import { Badge, Tag, Typography } from 'antd'
import './settingsKit.css'

/** 左侧导航的一项（对应右侧一个面板） */
export interface SettingsNavItem {
  key: string
  label: string
}

/** 左侧导航的一组（分类标题 + 其下的项） */
export interface SettingsNavCategory {
  key: string
  label: string
  items: SettingsNavItem[]
}

interface SettingsNavProps {
  categories: SettingsNavCategory[]
  activeKey: string
  onSelect: (key: string) => void
  /** 有未保存改动的项（项上画圆点、所属分类上也画一个） */
  dirtyKeys?: Set<string>
  ariaLabel?: string
}

/**
 * 设置类页面的**左侧分类导航**。
 *
 * 为什么抽成组件：系统设置（14 个分组）与支付与渠道（状态 + 3 个分组）用的是同一套
 * 「左导航 + 右单面板」布局。复制一份的代价不是 40 行 CSS，而是两处会慢慢分叉
 * ——「脏标记」这类细节漏一处，用户就会以为改动已经保存了。
 *
 * 分类只影响排版，不影响数据归属：分组顺序仍由后端 `SETTING_GROUPS` 决定，
 * 这里只决定「哪几项摆在一起」。
 */
export function SettingsNav({
  categories,
  activeKey,
  onSelect,
  dirtyKeys,
  ariaLabel = '设置分类',
}: SettingsNavProps) {
  const isDirty = (key: string) => !!dirtyKeys?.has(key)

  return (
    <aside className="settings-nav" aria-label={ariaLabel}>
      {categories.map((category) => (
        <div key={category.key} className="settings-nav__block">
          <div className="settings-nav__title">
            <span>{category.label}</span>
            {category.items.some((item) => isDirty(item.key)) && (
              <span className="settings-nav__dot" title="该分类有未保存的修改" />
            )}
          </div>
          {category.items.map((item) => {
            const active = item.key === activeKey
            return (
              <button
                key={item.key}
                type="button"
                className={`settings-nav__item${active ? ' is-active' : ''}`}
                aria-current={active ? 'true' : undefined}
                onClick={() => onSelect(item.key)}
              >
                <span>{item.label}</span>
                {isDirty(item.key) && <Badge status="warning" />}
              </button>
            )
          })}
        </div>
      ))}
    </aside>
  )
}

interface SettingsPanelProps {
  title: string
  description?: ReactNode
  /** 该面板有未保存改动（标题旁标「未保存」） */
  dirty?: boolean
  children: ReactNode
}

/** 右侧面板：标题 + 说明 + 内容（与 `SettingsNav` 配套） */
export function SettingsPanel({ title, description, dirty, children }: SettingsPanelProps) {
  return (
    <section className="settings-panel">
      <div className="settings-panel__head">
        <Typography.Title level={5} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        {dirty && <Tag color="orange">未保存</Tag>}
      </div>
      {description ? (
        <Typography.Text type="secondary" className="settings-panel__desc">
          {description}
        </Typography.Text>
      ) : null}
      <div className="settings-panel__body">{children}</div>
    </section>
  )
}
