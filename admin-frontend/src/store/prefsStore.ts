import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { isLocale } from '../i18n/messages'
import type { Locale } from '../i18n/messages'

/** 主题模式：跟随系统时由 `matchMedia` 实时决定 */
export type ThemeMode = 'light' | 'dark' | 'system'
/** 菜单布局：垂直（侧栏） / 水平（顶栏） / 混合（顶栏一级 + 侧栏二级） / 双列（分组栏 + 菜单栏） */
export type LayoutMode = 'side' | 'top' | 'mixed' | 'double'
/** 菜单风格：设计（默认，跟随品牌设计） / 暗色 / 亮色（与演示站三项一致） */
export type MenuStyle = 'design' | 'dark' | 'light'
/** 卡片盒子样式：描边 / 阴影 */
export type BoxStyle = 'border' | 'shadow'
/** 内容容器宽度：铺满 / 定宽居中 */
export type PageWidth = 'full' | 'fixed'
/** 标签页风格（演示站：胶囊 / 卡片 / 谷歌） */
export type TabStyle = 'capsule' | 'card' | 'google'
/** 页面切换动画（演示站 5 档） */
export type PageTransition = 'none' | 'fade' | 'slide-left' | 'slide-bottom' | 'slide-top'
/** 组件尺寸（演示站右上角「默认 / 大型 / 小型」） */
export type ComponentSize = 'default' | 'large' | 'small'

/** 基础配置里可开关的 18 项（与演示站逐项对应） */
export type ToggleKey =
  | 'showTabs'
  | 'accordion'
  | 'showMenuButton'
  | 'showFastEnter'
  | 'showRefreshButton'
  | 'showCrumbs'
  | 'showLanguage'
  | 'showMenuSearch'
  | 'showFullscreen'
  | 'showSizeSelect'
  | 'showNotification'
  | 'showNprogress'
  | 'colorWeak'
  | 'watermark'
  | 'showAppLogo'
  | 'showGuide'
  | 'grayMode'
  | 'aiAssistant'

export interface PrefsState {
  mode: ThemeMode
  layout: LayoutMode
  /** 界面语言：与主题/布局同属展示偏好，退出登录不丢 */
  locale: Locale
  /** 系统主题色（antd colorPrimary + CSS 变量 --app-primary 同源） */
  primaryColor: string
  menuStyle: MenuStyle
  boxStyle: BoxStyle
  pageWidth: PageWidth
  /** 侧栏菜单宽度（演示站 180~320，步长 10，默认 230） */
  menuWidth: number
  /** 标签页风格 */
  tabStyle: TabStyle
  /** 页面切换动画 */
  pageTransition: PageTransition
  /** 自定义圆角档位（0 / 0.25 / 0.5 / 0.75 / 1） */
  radius: number
  /** 组件尺寸 */
  componentSize: ComponentSize
  /** 新手引导是否已完成（随偏好同步到服务端，换浏览器不再重复弹出） */
  guideDone: boolean

  /** 是否显示顶部多标签栏 */
  showTabs: boolean
  /** 侧边菜单是否手风琴（同时只展开一个分组） */
  accordion: boolean
  /** 顶栏折叠/展开菜单按钮 */
  showMenuButton: boolean
  /** 顶栏快速入口 */
  showFastEnter: boolean
  /** 顶栏刷新按钮 */
  showRefreshButton: boolean
  /** 顶栏面包屑 */
  showCrumbs: boolean
  /** 顶栏语言切换 */
  showLanguage: boolean
  /** 顶栏全局搜索 */
  showMenuSearch: boolean
  /** 顶栏全屏切换 */
  showFullscreen: boolean
  /** 顶栏组件尺寸切换 */
  showSizeSelect: boolean
  /** 顶栏通知入口（本项目为待办铃铛） */
  showNotification: boolean
  /** 顶部路由进度条 */
  showNprogress: boolean
  /** 色弱模式 */
  colorWeak: boolean
  /** 全局水印 */
  watermark: boolean
  /** 显示 Logo 与系统标题 */
  showAppLogo: boolean
  /** 登录后新手引导 */
  showGuide: boolean
  /** 灰色模式 */
  grayMode: boolean
  /** AI 助手浮动入口 */
  aiAssistant: boolean

  setMode: (mode: ThemeMode) => void
  setLayout: (layout: LayoutMode) => void
  setLocale: (locale: Locale) => void
  setPrimaryColor: (color: string) => void
  setMenuStyle: (style: MenuStyle) => void
  setBoxStyle: (style: BoxStyle) => void
  setPageWidth: (width: PageWidth) => void
  setShowTabs: (show: boolean) => void
  setAccordion: (accordion: boolean) => void
  /** 批量更新（设置抽屉里的数字/下拉/开关统一走这里，减少 18 个重复 setter） */
  setPrefs: (partial: Partial<PrefsState>) => void
  /** 合并服务端偏好（服务端优先，未知/非法字段回落当前值） */
  applyServerPrefs: (prefs: Record<string, unknown>) => void
  /** 恢复演示站默认配置 */
  resetPrefs: () => void
}

/** 默认系统主题色：与演示站一致（星蓝） */
export const DEFAULT_PRIMARY_COLOR = '#3F5CE8'

/** 演示站基础配置默认值（重置配置时恢复这一组） */
export const DEFAULT_PREFS = {
  mode: 'light' as ThemeMode,
  layout: 'side' as LayoutMode,
  locale: 'zh-CN' as Locale,
  primaryColor: DEFAULT_PRIMARY_COLOR,
  menuStyle: 'design' as MenuStyle,
  boxStyle: 'border' as BoxStyle,
  pageWidth: 'full' as PageWidth,
  menuWidth: 230,
  tabStyle: 'google' as TabStyle,
  pageTransition: 'slide-left' as PageTransition,
  radius: 0,
  componentSize: 'default' as ComponentSize,
  guideDone: false,
  showTabs: true,
  accordion: true,
  showMenuButton: true,
  showFastEnter: true,
  showRefreshButton: true,
  showCrumbs: true,
  showLanguage: true,
  showMenuSearch: true,
  showFullscreen: true,
  showSizeSelect: true,
  showNotification: true,
  showNprogress: true,
  colorWeak: false,
  watermark: false,
  showAppLogo: true,
  showGuide: true,
  grayMode: false,
  aiAssistant: false,
} as const

export const MENU_WIDTH_MIN = 180
export const MENU_WIDTH_MAX = 320
export const MENU_WIDTH_STEP = 10
export const RADIUS_VALUES = [0, 0.25, 0.5, 0.75, 1] as const

const LAYOUTS: LayoutMode[] = ['side', 'top', 'mixed', 'double']
const MENU_STYLES: MenuStyle[] = ['design', 'dark', 'light']
const BOX_STYLES: BoxStyle[] = ['border', 'shadow']
const PAGE_WIDTHS: PageWidth[] = ['full', 'fixed']
const TAB_STYLES: TabStyle[] = ['capsule', 'card', 'google']
const PAGE_TRANSITIONS: PageTransition[] = ['none', 'fade', 'slide-left', 'slide-bottom', 'slide-top']
const COMPONENT_SIZES: ComponentSize[] = ['default', 'large', 'small']

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value)
}

function isLayout(value: unknown): value is LayoutMode {
  return oneOf(value, LAYOUTS)
}

function isMenuStyle(value: unknown): value is MenuStyle {
  return oneOf(value, MENU_STYLES)
}

function isBoxStyle(value: unknown): value is BoxStyle {
  return oneOf(value, BOX_STYLES)
}

function isPageWidth(value: unknown): value is PageWidth {
  return oneOf(value, PAGE_WIDTHS)
}

function isThemeMode(value: unknown): value is ThemeMode {
  return oneOf(value, ['light', 'dark', 'system'])
}

function isTabStyle(value: unknown): value is TabStyle {
  return oneOf(value, TAB_STYLES)
}

function isPageTransition(value: unknown): value is PageTransition {
  return oneOf(value, PAGE_TRANSITIONS)
}

function isComponentSize(value: unknown): value is ComponentSize {
  return oneOf(value, COMPONENT_SIZES)
}

function isMenuWidth(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isFinite(value) &&
    value >= MENU_WIDTH_MIN &&
    value <= MENU_WIDTH_MAX
  )
}

function isRadius(value: unknown): value is number {
  return typeof value === 'number' && (RADIUS_VALUES as readonly number[]).includes(value)
}

function isHexColor(value: unknown): value is string {
  return typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value)
}

function boolOr(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

/** 逐项校验外部来源（localStorage / 服务端）的偏好，非法值一律回落到当前值 */
function sanitizePrefs(saved: Record<string, unknown>, current: PrefsState) {
  return {
    mode: isThemeMode(saved.mode) ? saved.mode : current.mode,
    locale: isLocale(saved.locale) ? saved.locale : current.locale,
    layout: isLayout(saved.layout) ? saved.layout : current.layout,
    primaryColor: isHexColor(saved.primaryColor) ? saved.primaryColor : current.primaryColor,
    menuStyle: isMenuStyle(saved.menuStyle) ? saved.menuStyle : current.menuStyle,
    boxStyle: isBoxStyle(saved.boxStyle) ? saved.boxStyle : current.boxStyle,
    pageWidth: isPageWidth(saved.pageWidth) ? saved.pageWidth : current.pageWidth,
    menuWidth: isMenuWidth(saved.menuWidth) ? saved.menuWidth : current.menuWidth,
    tabStyle: isTabStyle(saved.tabStyle) ? saved.tabStyle : current.tabStyle,
    pageTransition: isPageTransition(saved.pageTransition)
      ? saved.pageTransition
      : current.pageTransition,
    radius: isRadius(saved.radius) ? saved.radius : current.radius,
    componentSize: isComponentSize(saved.componentSize)
      ? saved.componentSize
      : current.componentSize,
    guideDone: boolOr(saved.guideDone, current.guideDone),
    showTabs: boolOr(saved.showTabs, current.showTabs),
    accordion: boolOr(saved.accordion, current.accordion),
    showMenuButton: boolOr(saved.showMenuButton, current.showMenuButton),
    showFastEnter: boolOr(saved.showFastEnter, current.showFastEnter),
    showRefreshButton: boolOr(saved.showRefreshButton, current.showRefreshButton),
    showCrumbs: boolOr(saved.showCrumbs, current.showCrumbs),
    showLanguage: boolOr(saved.showLanguage, current.showLanguage),
    showMenuSearch: boolOr(saved.showMenuSearch, current.showMenuSearch),
    showFullscreen: boolOr(saved.showFullscreen, current.showFullscreen),
    showSizeSelect: boolOr(saved.showSizeSelect, current.showSizeSelect),
    showNotification: boolOr(saved.showNotification, current.showNotification),
    showNprogress: boolOr(saved.showNprogress, current.showNprogress),
    colorWeak: boolOr(saved.colorWeak, current.colorWeak),
    watermark: boolOr(saved.watermark, current.watermark),
    showAppLogo: boolOr(saved.showAppLogo, current.showAppLogo),
    showGuide: boolOr(saved.showGuide, current.showGuide),
    grayMode: boolOr(saved.grayMode, current.grayMode),
    aiAssistant: boolOr(saved.aiAssistant, current.aiAssistant),
  }
}

/**
 * 界面偏好（主题 / 布局 / 语言 / 主题色 / 显示项）。
 *
 * 为什么单独一个 store 而不是塞进 authStore：这几项是**纯展示偏好**，登录态被清空
 * （退出、401）时不该跟着丢 —— 用户下次登录还希望是暗色、英文。
 *
 * 持久化到 localStorage（`persist`）：刷新后保持；键名前缀 `adm-` 与其它本地存储区分。
 * 只存用户显式选过的值，不存「当前解析出来的亮/暗」—— 否则改了系统设置就不跟手。
 */
export const usePrefsStore = create<PrefsState>()(
  persist(
    (set) => ({
      ...DEFAULT_PREFS,
      setMode: (mode) => set({ mode }),
      setLayout: (layout) => set({ layout }),
      setLocale: (locale) => set({ locale }),
      setPrimaryColor: (primaryColor) => set({ primaryColor }),
      setMenuStyle: (menuStyle) => set({ menuStyle }),
      setBoxStyle: (boxStyle) => set({ boxStyle }),
      setPageWidth: (pageWidth) => set({ pageWidth }),
      setShowTabs: (showTabs) => set({ showTabs }),
      setAccordion: (accordion) => set({ accordion }),
      setPrefs: (partial) => set(partial),
      applyServerPrefs: (serverPrefs) =>
        set((current) => sanitizePrefs(serverPrefs, current)),
      resetPrefs: () => set({ ...DEFAULT_PREFS }),
    }),
    {
      name: 'adm-ui-prefs',
      version: 1,
      // 老版本持久化里没有新字段（或被人为改坏）：逐项回落到合法默认值，
      // 不能让 translate / ConfigProvider 拿到非法值。
      merge: (persisted, current) => {
        const saved = (persisted ?? {}) as Record<string, unknown>
        return {
          ...current,
          ...saved,
          ...sanitizePrefs(saved, current),
        }
      },
    },
  ),
)

/** 把 `system` 解析成实际的亮/暗（非浏览器环境回退亮色，便于测试） */
export function resolveMode(mode: ThemeMode): 'light' | 'dark' {
  if (mode !== 'system') return mode
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** 菜单风格解析：只有显式「暗色」才走深色侧栏，设计与亮色均为浅色 */
export function resolveMenuStyle(style: MenuStyle): 'light' | 'dark' {
  return style === 'dark' ? 'dark' : 'light'
}
