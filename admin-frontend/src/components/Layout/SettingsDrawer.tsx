import type { ReactNode } from 'react'
import {
  App as AntdApp,
  Button,
  Drawer,
  InputNumber,
  Segmented,
  Select,
  Switch,
  Typography,
} from 'antd'
import { CheckOutlined, CopyOutlined, ReloadOutlined } from '@ant-design/icons'
import { usePrefsStore } from '../../store/prefsStore'
import {
  MENU_WIDTH_MAX,
  MENU_WIDTH_MIN,
  MENU_WIDTH_STEP,
  RADIUS_VALUES,
} from '../../store/prefsStore'
import type {
  BoxStyle,
  ComponentSize,
  LayoutMode,
  MenuStyle,
  PageTransition,
  PageWidth,
  TabStyle,
  ThemeMode,
  ToggleKey,
} from '../../store/prefsStore'
import { LOCALE_LABELS, useI18n } from '../../i18n'
import type { Locale } from '../../i18n'
import { THEME_COLORS } from '../../theme'
import { flushPreferencesNow } from '../../hooks/usePreferencesSync'

/** 基础配置 18 项：label key → store 字段（与演示站顺序/文案逐项对应） */
const BASIC_TOGGLES: { key: ToggleKey; label: string }[] = [
  { key: 'showTabs', label: 'settings.showTabs' },
  { key: 'accordion', label: 'settings.accordion' },
  { key: 'showMenuButton', label: 'settings.showMenuButton' },
  { key: 'showFastEnter', label: 'settings.showFastEnter' },
  { key: 'showRefreshButton', label: 'settings.showRefreshButton' },
  { key: 'showCrumbs', label: 'settings.showCrumbs' },
  { key: 'showLanguage', label: 'settings.showLanguage' },
  { key: 'showMenuSearch', label: 'settings.showMenuSearch' },
  { key: 'showFullscreen', label: 'settings.showFullscreen' },
  { key: 'showSizeSelect', label: 'settings.showSizeSelect' },
  { key: 'showNotification', label: 'settings.showNotification' },
  { key: 'showNprogress', label: 'settings.showNprogress' },
  { key: 'colorWeak', label: 'settings.colorWeak' },
  { key: 'watermark', label: 'settings.watermark' },
  { key: 'showAppLogo', label: 'settings.showAppLogo' },
  { key: 'showGuide', label: 'settings.showGuide' },
  { key: 'grayMode', label: 'settings.grayMode' },
  { key: 'aiAssistant', label: 'settings.aiAssistant' },
]

type PreviewOption<T extends string> = { value: T; label: string }

/**
 * 图片预览选择组（对应演示站的布局缩略图卡片）。
 *
 * 用 `button` + `role="radio"` 实现：原生按钮天然支持 Tab 聚焦与 Enter/Space 切换，
 * `aria-checked` 暴露选中态；缩略图纯 CSS 绘制，不引入图片资源。
 */
function PreviewRadioGroup<T extends string>({
  label,
  value,
  options,
  onChange,
  art,
}: {
  label: string
  value: T
  options: PreviewOption<T>[]
  onChange: (value: T) => void
  art: (value: T) => ReactNode
}) {
  return (
    <div className="settings-drawer__section">
      <Typography.Text type="secondary" className="settings-drawer__label">
        {label}
      </Typography.Text>
      <div className="settings-drawer__previews" role="radiogroup" aria-label={label}>
        {options.map((option) => {
          const active = value === option.value
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={active}
              className={`settings-drawer__preview${active ? ' is-active' : ''}`}
              onClick={() => onChange(option.value)}
            >
              <span className="settings-drawer__preview-art" aria-hidden>
                {art(option.value)}
              </span>
              <span className="settings-drawer__preview-label">{option.label}</span>
              {active && <CheckOutlined className="settings-drawer__preview-check" />}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** 布局缩略图：色块代表侧栏/顶栏/内容区 */
function LayoutArt({ value }: { value: LayoutMode }) {
  return (
    <span className={`pv pv-layout pv-layout--${value}`}>
      <i className="pv-bar pv-bar--side" />
      <i className="pv-bar pv-bar--sub" />
      <i className="pv-bar pv-bar--top" />
      <i className="pv-panel" />
    </span>
  )
}

/** 菜单风格缩略图：模拟侧栏的三种配色 */
function MenuStyleArt({ value }: { value: MenuStyle }) {
  return (
    <span className={`pv pv-menu pv-menu--${value}`}>
      <i className="pv-menu-item is-active" />
      <i className="pv-menu-item" />
      <i className="pv-menu-item" />
    </span>
  )
}

/** 盒子样式缩略图：描边 vs 阴影 */
function BoxStyleArt({ value }: { value: BoxStyle }) {
  return (
    <span className="pv pv-box">
      <i className={`pv-card pv-card--${value}`} />
    </span>
  )
}

/** 容器宽度缩略图：铺满 vs 居中定宽 */
function PageWidthArt({ value }: { value: PageWidth }) {
  return (
    <span className={`pv pv-width pv-width--${value}`}>
      <i className="pv-content" />
    </span>
  )
}

/**
 * 界面设置抽屉（对照 FastAPIAdmin 演示站设置区，逐项对齐）。
 *
 * 所有选项都写进 `prefsStore`（localStorage 持久化），并在 main.tsx 的 ThemeShell
 * 里同时作用于 antd ConfigProvider 与 `--app-*` CSS 变量，刷新后保持。
 *
 * antd 6 约定：Drawer 用 `size`（不再用 `width`），Tag 用 `variant`（不再用 `bordered`）。
 */
export default function SettingsDrawer({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const { t, locale, setLocale } = useI18n()
  // 基础配置 18 个开关要实时反映勾选状态：整体订阅一次（抽屉只在打开时渲染，成本可忽略）
  const prefs = usePrefsStore()
  const mode = prefs.mode
  const layout = prefs.layout
  const menuStyle = prefs.menuStyle
  const primaryColor = prefs.primaryColor
  const boxStyle = prefs.boxStyle
  const pageWidth = prefs.pageWidth
  const menuWidth = prefs.menuWidth
  const tabStyle = prefs.tabStyle
  const pageTransition = prefs.pageTransition
  const radius = prefs.radius
  const componentSize = prefs.componentSize
  const setPrefs = usePrefsStore((s) => s.setPrefs)
  const resetPrefs = usePrefsStore((s) => s.resetPrefs)
  const { modal, message } = AntdApp.useApp()

  const section = (label: string, control: ReactNode) => (
    <div className="settings-drawer__section">
      <Typography.Text type="secondary" className="settings-drawer__label">
        {label}
      </Typography.Text>
      {control}
    </div>
  )

  /** 复制当前配置为 JSON（演示站「复制配置」：方便把一套外观带到另一台设备） */
  const handleCopy = async () => {
    const state = usePrefsStore.getState()
    const config = {
      mode: state.mode,
      layout: state.layout,
      locale: state.locale,
      primaryColor: state.primaryColor,
      menuStyle: state.menuStyle,
      boxStyle: state.boxStyle,
      pageWidth: state.pageWidth,
      menuWidth: state.menuWidth,
      tabStyle: state.tabStyle,
      pageTransition: state.pageTransition,
      radius: state.radius,
      componentSize: state.componentSize,
      showTabs: state.showTabs,
      accordion: state.accordion,
      showMenuButton: state.showMenuButton,
      showFastEnter: state.showFastEnter,
      showRefreshButton: state.showRefreshButton,
      showCrumbs: state.showCrumbs,
      showLanguage: state.showLanguage,
      showMenuSearch: state.showMenuSearch,
      showFullscreen: state.showFullscreen,
      showSizeSelect: state.showSizeSelect,
      showNotification: state.showNotification,
      showNprogress: state.showNprogress,
      colorWeak: state.colorWeak,
      watermark: state.watermark,
      showAppLogo: state.showAppLogo,
      showGuide: state.showGuide,
      grayMode: state.grayMode,
      aiAssistant: state.aiAssistant,
      guideDone: state.guideDone,
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(config, null, 2))
      message.success(t('settings.copied'))
    } catch {
      message.error(t('settings.copyFailed'))
    }
  }

  /** 重置配置：恢复默认 → 立即写回服务端（避免刷新后又被旧偏好覆盖）→ 刷新页面 */
  const handleReset = () => {
    modal.confirm({
      title: t('settings.resetConfig'),
      content: t('settings.resetConfirmDesc'),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        resetPrefs()
        // 离线时最多等 2 秒：刷新后以服务端旧值为准，但不让用户卡在确认框里
        await Promise.race([
          flushPreferencesNow(),
          new Promise((resolve) => window.setTimeout(resolve, 2000)),
        ])
        window.location.reload()
      },
    })
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      placement="right"
      size={320}
      title={t('settings.title')}
      styles={{ body: { paddingTop: 16 } }}
    >
      <div className="settings-drawer">
        {section(
          t('settings.appearance'),
          <Segmented
            block
            value={mode}
            onChange={(value) => setPrefs({ mode: value as ThemeMode })}
            options={[
              { label: t('settings.modeLight'), value: 'light' },
              { label: t('settings.modeDark'), value: 'dark' },
              { label: t('settings.modeSystem'), value: 'system' },
            ]}
          />,
        )}

        <PreviewRadioGroup
          label={t('settings.menuLayout')}
          value={layout}
          onChange={(value) => setPrefs({ layout: value })}
          options={[
            { label: t('settings.layoutSide'), value: 'side' },
            { label: t('settings.layoutTop'), value: 'top' },
            { label: t('settings.layoutMixed'), value: 'mixed' },
            { label: t('settings.layoutDouble'), value: 'double' },
          ]}
          art={(value) => <LayoutArt value={value} />}
        />

        <PreviewRadioGroup
          label={t('settings.menuStyle')}
          value={menuStyle}
          onChange={(value) => setPrefs({ menuStyle: value })}
          options={[
            { label: t('settings.menuStyleDesign'), value: 'design' },
            { label: t('settings.menuDark'), value: 'dark' },
            { label: t('settings.menuLight'), value: 'light' },
          ]}
          art={(value) => <MenuStyleArt value={value} />}
        />

        {section(
          t('settings.primaryColor'),
          <div className="settings-drawer__swatches">
            {THEME_COLORS.map((color) => {
              const active = primaryColor.toUpperCase() === color.value
              const label = t(`settings.color${color.key[0].toUpperCase()}${color.key.slice(1)}`)
              return (
                <button
                  key={color.value}
                  type="button"
                  className={`settings-drawer__swatch${active ? ' is-active' : ''}`}
                  style={{ background: color.value }}
                  aria-label={label}
                  aria-pressed={active}
                  title={`${label} ${color.value}`}
                  onClick={() => setPrefs({ primaryColor: color.value })}
                >
                  {active && <CheckOutlined />}
                </button>
              )
            })}
          </div>,
        )}

        <PreviewRadioGroup
          label={t('settings.boxStyle')}
          value={boxStyle}
          onChange={(value) => setPrefs({ boxStyle: value })}
          options={[
            { label: t('settings.boxBorder'), value: 'border' },
            { label: t('settings.boxShadow'), value: 'shadow' },
          ]}
          art={(value) => <BoxStyleArt value={value} />}
        />

        <PreviewRadioGroup
          label={t('settings.pageWidth')}
          value={pageWidth}
          onChange={(value) => setPrefs({ pageWidth: value })}
          options={[
            { label: t('settings.widthFull'), value: 'full' },
            { label: t('settings.widthFixed'), value: 'fixed' },
          ]}
          art={(value) => <PageWidthArt value={value} />}
        />

        <div className="settings-drawer__section">
          <Typography.Text type="secondary" className="settings-drawer__label">
            {t('settings.basic')}
          </Typography.Text>
          {BASIC_TOGGLES.map((item) => (
            <div key={item.key} className="settings-drawer__row">
              <span>{t(item.label)}</span>
              <Switch
                checked={prefs[item.key]}
                onChange={(checked) => setPrefs({ [item.key]: checked } as never)}
                aria-label={t(item.label)}
              />
            </div>
          ))}
        </div>

        {section(
          t('settings.menuWidth'),
          <InputNumber
            min={MENU_WIDTH_MIN}
            max={MENU_WIDTH_MAX}
            step={MENU_WIDTH_STEP}
            value={menuWidth}
            onChange={(value) =>
              setPrefs({
                menuWidth: typeof value === 'number' ? value : MENU_WIDTH_MIN,
              })
            }
            style={{ width: '100%' }}
            aria-label={t('settings.menuWidth')}
          />,
        )}

        {section(
          t('settings.tabStyle'),
          <Select
            value={tabStyle}
            onChange={(value) => setPrefs({ tabStyle: value as TabStyle })}
            style={{ width: '100%' }}
            options={[
              { label: t('settings.tabStyleCapsule'), value: 'capsule' },
              { label: t('settings.tabStyleCard'), value: 'card' },
              { label: t('settings.tabStyleGoogle'), value: 'google' },
            ]}
          />,
        )}

        {section(
          t('settings.pageTransition'),
          <Select
            value={pageTransition}
            onChange={(value) => setPrefs({ pageTransition: value as PageTransition })}
            style={{ width: '100%' }}
            options={[
              { label: t('settings.ptNone'), value: 'none' },
              { label: t('settings.ptFade'), value: 'fade' },
              { label: t('settings.ptSlideLeft'), value: 'slide-left' },
              { label: t('settings.ptSlideBottom'), value: 'slide-bottom' },
              { label: t('settings.ptSlideTop'), value: 'slide-top' },
            ]}
          />,
        )}

        {section(
          t('settings.radius'),
          <Select
            value={radius}
            onChange={(value) => setPrefs({ radius: Number(value) })}
            style={{ width: '100%' }}
            options={RADIUS_VALUES.map((value) => ({
              label: String(value),
              value,
            }))}
          />,
        )}

        {section(
          t('settings.componentSize'),
          <Segmented
            block
            value={componentSize}
            onChange={(value) => setPrefs({ componentSize: value as ComponentSize })}
            options={[
              { label: t('settings.sizeDefault'), value: 'default' },
              { label: t('settings.sizeLarge'), value: 'large' },
              { label: t('settings.sizeSmall'), value: 'small' },
            ]}
          />,
        )}

        {section(
          t('settings.language'),
          <Segmented
            block
            value={locale}
            onChange={(value) => setLocale(value as Locale)}
            options={(['zh-CN', 'en-US'] as Locale[]).map((value) => ({
              label: LOCALE_LABELS[value],
              value,
            }))}
          />,
        )}

        <div className="settings-drawer__actions">
          <Button icon={<CopyOutlined />} onClick={() => void handleCopy()}>
            {t('settings.copyConfig')}
          </Button>
          <Button danger icon={<ReloadOutlined />} onClick={handleReset}>
            {t('settings.resetConfig')}
          </Button>
        </div>
      </div>
    </Drawer>
  )
}
