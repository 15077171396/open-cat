import { theme as antdTheme } from 'antd'
import type { ThemeConfig } from 'antd'
import type { MenuStyle } from './store/prefsStore'

/**
 * 管理端主题 token（设计规范见 docs/07-开源文档/06-UI设计规范.md）。
 *
 * 视觉基线：gi-demo（Gi Admin Pro）—— 蓝 #165DFF + 橙 #FF9A2E 点缀、
 * 4px 小圆角、浅灰画布 #F5F7FD、白色页面卡、无卡片阴影。
 * 颜色与形状集中在这里，**页面里不要再写死十六进制与像素**。
 *
 * 可读性红线（改造时不要回退）：
 *   `colorTextSecondary` 保持 #5C5C5C（白底约 6.5:1），不许回到 antd 默认的
 *   rgba(0,0,0,.45)（3.35:1，低于 WCAG AA）。
 *   `colorError` 用 #CF1322（白底 5.5:1）而非 gi-demo 的 #F53F3F（3.7:1）。
 *   橙色 #FF9A2E 只做装饰（卡片标题竖条），不承担状态文字。
 */

/** 品牌色：主色白字对比度 5.15:1，达 WCAG AA */
export const BRAND = {
  primary: '#165DFF',
  primaryHover: '#4080FF',
  primaryActive: '#0E42D2',
  primaryWeak: '#E8F3FF',
  accent: '#FF9A2E',
  gradient: 'linear-gradient(135deg, #165DFF 0%, #0E42D2 100%)',
} as const

/** 画布与卡片：层次靠色差而非阴影，卡片一律无阴影 */
export const SURFACE = {
  /** 页面画布 */
  canvas: '#F5F7FD',
  /** 卡片底色 */
  card: '#FFFFFF',
  /** 分隔线与侧栏描边 */
  border: '#E5E6EB',
  /** 卡片、表格分隔线 */
  borderLight: '#EBEEF5',
  /** 表头等次级填充 */
  subtle: '#FAFAFA',
  /** hover / 中性标签填充 */
  fill: '#F2F3F5',
  /** 菜单选中底 */
  selected: '#F2F3F5',
} as const

export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: BRAND.primary,
    colorInfo: BRAND.primary,
    colorSuccess: '#16A34A',
    colorWarning: '#D97706',
    colorError: '#CF1322',
    colorLink: BRAND.primary,

    colorText: '#121314',
    colorTextSecondary: '#5C5C5C',
    colorTextTertiary: '#909399',
    colorTextDescription: '#5C5C5C',
    colorTextDisabled: '#C2C2C2',

    colorBgLayout: SURFACE.canvas,
    colorBgContainer: SURFACE.card,
    colorBgElevated: SURFACE.card,
    colorBorder: '#DCDFE6',
    colorBorderSecondary: SURFACE.border,
    boxShadowTertiary: 'none',

    // 形状：全站直角，运行时由 getRadiusTokens 按设置档位覆盖
    borderRadius: 0,
    borderRadiusLG: 0,
    borderRadiusSM: 0,
    controlHeight: 32,
    controlHeightLG: 40,
    controlHeightSM: 24,

    fontSize: 14,
  },
  components: {
    Layout: {
      headerHeight: 56,
      headerPadding: '0 16px',
      bodyBg: SURFACE.canvas,
      headerBg: SURFACE.card,
      siderBg: SURFACE.card,
    },
    Menu: {
      itemSelectedColor: BRAND.primary,
      itemSelectedBg: SURFACE.fill,
      itemHoverBg: SURFACE.fill,
      itemBorderRadius: 0,
      itemHeight: 40,
      itemMarginInline: 8,
      itemMarginBlock: 2,
      collapsedWidth: 80,
      iconSize: 16,
      groupTitleColor: '#5C5C5C',
      groupTitleFontSize: 12,
    },
    Table: {
      headerBg: SURFACE.subtle,
      headerColor: '#5C5C5C',
      headerSplitColor: 'transparent',
      rowHoverBg: '#F7F9FC',
      cellPaddingBlock: 12,
      borderColor: SURFACE.borderLight,
    },
    Card: { paddingLG: 16, borderRadiusLG: 0, headerHeight: 44, headerFontSize: 16 },
    Button: { primaryShadow: 'none', defaultShadow: 'none' },
    Modal: { borderRadiusLG: 0, titleFontSize: 16 },
    Tabs: {
      itemSelectedColor: BRAND.primary,
      inkBarColor: BRAND.primary,
      titleFontSize: 13,
      horizontalItemPadding: '8px 12px',
    },
    Pagination: { itemActiveBg: BRAND.primary, itemSize: 28 },
    Input: {
      activeBorderColor: BRAND.primary,
      hoverBorderColor: BRAND.primaryHover,
      activeShadow: '0 0 0 2px rgba(22, 93, 255, 0.12)',
    },
    Tag: { defaultBg: SURFACE.fill, defaultColor: '#5C5C5C' },
  },
}

/** 暗色表面：与 tokens.css 的 [data-theme='dark'] 一一对应 */
export const SURFACE_DARK = {
  canvas: '#0F1115',
  card: '#161A21',
  border: '#262B35',
  borderLight: '#20252E',
  subtle: '#1B2029',
  fill: '#21262F',
  selected: '#1E2A54',
} as const

/**
 * 暗色 theme config：只覆盖「会随底色反向变化」的 token。
 * 其余（圆角、控件高度、间距）亮暗共用，避免两套配置慢慢走偏。
 */
const darkThemeConfig: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: '#4080FF',
    colorInfo: '#4080FF',
    colorSuccess: '#3FB950',
    colorWarning: '#D29922',
    colorError: '#F85149',
    colorLink: '#6EA8FE',

    colorText: '#E6E9EF',
    colorTextSecondary: '#A6ADBB',
    colorTextTertiary: '#8B93A1',
    colorTextDescription: '#A6ADBB',
    colorTextDisabled: '#5F6672',

    colorBgLayout: SURFACE_DARK.canvas,
    colorBgContainer: SURFACE_DARK.card,
    colorBgElevated: '#1B2029',
    colorBorder: '#3A4250',
    colorBorderSecondary: SURFACE_DARK.border,
    boxShadowTertiary: 'none',

    borderRadius: 0,
    borderRadiusLG: 0,
    borderRadiusSM: 0,
    controlHeight: 32,
    controlHeightLG: 40,
    controlHeightSM: 24,

    fontSize: 14,
  },
  components: {
    Layout: {
      headerHeight: 56,
      headerPadding: '0 16px',
      bodyBg: SURFACE_DARK.canvas,
      headerBg: SURFACE_DARK.card,
      siderBg: SURFACE_DARK.card,
    },
    Menu: {
      itemSelectedColor: '#6AA1FF',
      itemSelectedBg: SURFACE_DARK.selected,
      itemHoverBg: SURFACE_DARK.fill,
      itemBorderRadius: 0,
      itemHeight: 40,
      itemMarginInline: 8,
      itemMarginBlock: 2,
      collapsedWidth: 80,
      iconSize: 16,
      groupTitleColor: '#8B93A1',
      groupTitleFontSize: 12,
    },
    Table: {
      headerBg: SURFACE_DARK.subtle,
      headerColor: '#A6ADBB',
      headerSplitColor: 'transparent',
      rowHoverBg: SURFACE_DARK.fill,
      cellPaddingBlock: 12,
      borderColor: SURFACE_DARK.borderLight,
    },
    Card: { paddingLG: 16, borderRadiusLG: 0, headerHeight: 44, headerFontSize: 16 },
    Button: { primaryShadow: 'none', defaultShadow: 'none' },
    Modal: { borderRadiusLG: 0, titleFontSize: 16 },
    Tabs: {
      itemSelectedColor: '#6AA1FF',
      inkBarColor: '#4080FF',
      titleFontSize: 13,
      horizontalItemPadding: '8px 12px',
    },
    Pagination: { itemActiveBg: '#4080FF', itemSize: 28 },
    Input: {
      activeBorderColor: '#4080FF',
      hoverBorderColor: '#6AA1FF',
      activeShadow: '0 0 0 2px rgba(64, 128, 255, 0.18)',
    },
    Tag: { defaultBg: SURFACE_DARK.fill, defaultColor: '#A6ADBB' },
  },
}

/** 设置面板里的系统主题色预设：与演示站 7 色逐项一致（星蓝 / 紫 / 蓝 / 绿 / 青 / 橙 / 粉） */
export const THEME_COLORS = [
  { key: 'blue', value: '#3F5CE8' },
  { key: 'purple', value: '#B48DF3' },
  { key: 'sky', value: '#1D84FF' },
  { key: 'green', value: '#60C041' },
  { key: 'cyan', value: '#38C0FC' },
  { key: 'orange', value: '#F9901F' },
  { key: 'pink', value: '#FF80C8' },
] as const

/**
 * 自定义圆角档位 → 实际像素（对照演示站 0.75 档：base 8 / small 6 / 卡片 12）。
 * 0 档是完全直角；其余档位按 4/6/8/12 递增。
 */
export function getRadiusTokens(factor: number): {
  base: number
  sm: number
  xs: number
  lg: number
} {
  const base = factor <= 0 ? 0 : factor <= 0.25 ? 4 : factor <= 0.5 ? 6 : factor <= 0.75 ? 8 : 12
  if (base === 0) return { base: 0, sm: 0, xs: 0, lg: 0 }
  return {
    base,
    sm: Math.max(4, base - 2),
    xs: Math.max(4, base - 4),
    lg: base + 4,
  }
}

/** 圆角写入 CSS 变量（手写 `.app-*` 样式与 antd token 同源） */
export function applyRadiusCssVars(factor: number): void {
  if (typeof document === 'undefined') return
  const { base, sm, xs, lg } = getRadiusTokens(factor)
  const root = document.documentElement.style
  root.setProperty('--app-radius', `${base}px`)
  root.setProperty('--app-radius-sm', `${sm}px`)
  root.setProperty('--app-radius-xs', `${xs}px`)
  root.setProperty('--app-radius-lg', `${lg}px`)
}

interface Rgb {
  r: number
  g: number
  b: number
}

function parseHex(hex: string): Rgb | null {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim())
  if (!match) return null
  const value = parseInt(match[1], 16)
  return { r: (value >> 16) & 255, g: (value >> 8) & 255, b: value & 255 }
}

function toHex({ r, g, b }: Rgb): string {
  const part = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')
  return `#${part(r)}${part(g)}${part(b)}`.toUpperCase()
}

/** 把 `base` 往 `target` 混合 `weight`（0~1，weight 为目标色占比） */
function mix(base: string, target: string, weight: number): string {
  const a = parseHex(base)
  const b = parseHex(target)
  if (!a || !b) return base
  return toHex({
    r: a.r + (b.r - a.r) * weight,
    g: a.g + (b.g - a.g) * weight,
    b: a.b + (b.b - a.b) * weight,
  })
}

function rgba(hex: string, alpha: number): string {
  const rgb = parseHex(hex)
  if (!rgb) return `rgba(22, 93, 255, ${alpha})`
  return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha})`
}

/** 由主色派生 hover / active / 浅底 / 描边，避免只换主色后其余蓝色仍是旧值 */
export function getPrimaryPalette(primary: string, mode: 'light' | 'dark') {
  const safe = parseHex(primary) ? primary.toUpperCase() : BRAND.primary
  const isDark = mode === 'dark'
  return {
    primary: safe,
    hover: mix(safe, '#FFFFFF', isDark ? 0.22 : 0.15),
    active: mix(safe, '#000000', 0.12),
    weak: isDark ? mix(safe, SURFACE_DARK.canvas, 0.78) : mix(safe, '#FFFFFF', 0.9),
    border: isDark ? mix(safe, SURFACE_DARK.card, 0.62) : mix(safe, '#FFFFFF', 0.72),
    link: isDark ? mix(safe, '#FFFFFF', 0.24) : safe,
  }
}

/**
 * 把主题色写进 CSS 变量（`--app-*` 手写样式与 antd 的 colorPrimary 同源）。
 *
 * 内联样式优先级高于 tokens.css 里 `[data-theme='dark']` 的静态覆盖，
 * 因此亮/暗两套都能即时换色；主题模式切换时再调一次即可重新派生。
 */
export function applyPrimaryCssVars(primary: string, mode: 'light' | 'dark'): void {
  if (typeof document === 'undefined') return
  const palette = getPrimaryPalette(primary, mode)
  const root = document.documentElement.style
  root.setProperty('--app-primary', palette.primary.toLowerCase())
  root.setProperty('--app-primary-hover', palette.hover.toLowerCase())
  root.setProperty('--app-primary-active', palette.active.toLowerCase())
  root.setProperty('--app-primary-weak', palette.weak.toLowerCase())
  root.setProperty('--app-primary-border', palette.border.toLowerCase())
  root.setProperty('--app-link', palette.link.toLowerCase())
  root.setProperty('--app-info-bg', palette.weak.toLowerCase())
}

/**
 * 按模式取主题：亮色用 gi-demo 基线，暗色只覆盖必要 token。
 * 调用方传入 `resolveMode()` 的结果（'light' | 'dark'）。
 *
 * `primary` 来自设置面板的系统主题色；`menuStyle` 为 dark 时额外提供
 * antd Menu 的 dark* token，实现「亮色页面 + 深色侧栏」的组合。
 * `radiusFactor` 对应设置抽屉的 5 档圆角。
 */
export function getThemeConfig(
  mode: 'light' | 'dark',
  primary: string = BRAND.primary,
  menuStyle: MenuStyle = 'design',
  radiusFactor = 0,
): ThemeConfig {
  const palette = getPrimaryPalette(primary, mode)
  const base = mode === 'dark' ? darkThemeConfig : themeConfig
  const radii = getRadiusTokens(radiusFactor)
  const menuDark = menuStyle === 'dark'
  const darkMenuTokens = menuDark
    ? {
        darkItemBg: '#001529',
        darkSubMenuItemBg: '#000C17',
        darkItemColor: 'rgba(255, 255, 255, 0.75)',
        darkItemHoverBg: 'rgba(255, 255, 255, 0.08)',
        darkItemSelectedBg: palette.primary,
        darkItemSelectedColor: '#FFFFFF',
        darkGroupTitleColor: 'rgba(255, 255, 255, 0.65)',
      }
    : {}

  return {
    ...base,
    token: {
      ...base.token,
      colorPrimary: palette.primary,
      colorInfo: palette.primary,
      colorLink: palette.link,
      borderRadius: radii.base,
      borderRadiusLG: radii.lg,
      borderRadiusSM: radii.sm,
      borderRadiusXS: radii.xs,
    },
    components: {
      ...base.components,
      Menu: {
        ...base.components?.Menu,
        // 暗色下主色直接做文字会偏暗（如深绿 #15803D 对比不足），选中文字用提亮后的链接色
        itemSelectedColor: palette.link,
        itemSelectedBg: palette.weak,
        itemBorderRadius: radii.base,
        ...darkMenuTokens,
      },
      // 卡片 / 弹窗的圆角跟随设置档位（默认 0 = 直角）
      Card: { ...base.components?.Card, borderRadiusLG: radii.lg },
      Modal: { ...base.components?.Modal, borderRadiusLG: radii.lg },
      Tabs: {
        ...base.components?.Tabs,
        itemSelectedColor: palette.link,
        inkBarColor: palette.primary,
      },
      Pagination: { ...base.components?.Pagination, itemActiveBg: palette.primary },
      Input: {
        ...base.components?.Input,
        activeBorderColor: palette.primary,
        hoverBorderColor: palette.hover,
        activeShadow: `0 0 0 2px ${rgba(palette.primary, mode === 'dark' ? 0.18 : 0.12)}`,
      },
    },
  }
}
