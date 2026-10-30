import { theme as antdTheme } from 'antd'
import type { ThemeConfig } from 'antd'

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

    // 形状：卡片/控件/标签 4，弹窗/浮层 8
    borderRadius: 4,
    borderRadiusLG: 8,
    borderRadiusSM: 4,
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
      itemBorderRadius: 4,
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
    Card: { paddingLG: 16, borderRadiusLG: 4, headerHeight: 44, headerFontSize: 16 },
    Button: { primaryShadow: 'none', defaultShadow: 'none' },
    Modal: { borderRadiusLG: 8, titleFontSize: 16 },
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

    borderRadius: 4,
    borderRadiusLG: 8,
    borderRadiusSM: 4,
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
      itemBorderRadius: 4,
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
    Card: { paddingLG: 16, borderRadiusLG: 4, headerHeight: 44, headerFontSize: 16 },
    Button: { primaryShadow: 'none', defaultShadow: 'none' },
    Modal: { borderRadiusLG: 8, titleFontSize: 16 },
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

/**
 * 按模式取主题：亮色用 gi-demo 基线，暗色只覆盖必要 token。
 * 调用方传入 `resolveMode()` 的结果（'light' | 'dark'）。
 */
export function getThemeConfig(mode: 'light' | 'dark'): ThemeConfig {
  return mode === 'dark' ? darkThemeConfig : themeConfig
}
