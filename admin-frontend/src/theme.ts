import { theme as antdTheme } from 'antd'
import type { ThemeConfig } from 'antd'

/**
 * 管理端主题 token（设计规范见 docs/07-开源文档/06-UI设计规范.md）。
 *
 * 结构取自参考项目 `design/UI-GUIDELINES.md` 的硬性纪律：
 *   卡片间距 16 / 卡片圆角 12 / 控件圆角 8 / 页面内边距 16 / 顶栏 56 /
 *   表格行高 40、表头 44、无竖分隔线。
 * 颜色与形状集中在这里，**页面里不要再写死十六进制与像素**。
 *
 * 可读性红线（改造时不要回退）：
 *   此前没有 ConfigProvider，直接用 antd 默认 token：`colorTextSecondary`
 *   等为 `rgba(0,0,0,0.45)`，白底 **3.35:1**，低于 WCAG AA 的 4.5:1。
 *   后台大量用 `Typography.Text type="secondary"` 写字段说明、时间、计数，
 *   长时间盯着看更吃力，所以这里一并压深。
 *   `colorError` 用 red-7（#cf1322，白底 5.5:1）而非 red-5（#ff4d4f，3.27:1），
 *   否则删除类按钮的文字读不清。
 *   `colorTextDisabled` 刻意不压深：WCAG 1.4.3 豁免非活动控件。
 */

/**
 * 品牌色。参考项目用 #4F6BFF，白底作按钮底色时白字只有 **4.24:1**（AA 正文要求 4.5:1），
 * 这里下调到 #4263EB（**4.94:1**）——观感几乎一致，但实心按钮的文字达到 AA。
 * 想完全用同款色，把 primary 改回 '#4F6BFF' 即可，其余 token 不必动。
 */
export const BRAND = {
  primary: '#4263EB',
  primaryHover: '#5B6CFF',
  gradient: 'linear-gradient(135deg, #4263EB 0%, #6D5BFF 100%)',
} as const

/** 画布与卡片：层次靠色差而非阴影，避免满屏卡片阴影导致的"脏" */
export const SURFACE = {
  /** 页面画布 */
  canvas: '#F5F6F8',
  /** 卡片底色 */
  card: '#FFFFFF',
  /** 分隔线与侧栏描边 */
  border: '#E6E8EC',
  /** 表头等次级填充 */
  subtle: '#FAFBFC',
  /** 菜单选中底 */
  selected: '#EEF1FF',
} as const

export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: BRAND.primary,
    colorInfo: BRAND.primary,
    colorSuccess: '#16A34A',
    colorWarning: '#D97706',
    colorError: '#CF1322',
    colorLink: '#0958D9',

    colorText: '#1F2329',
    colorTextSecondary: '#595959',
    colorTextTertiary: '#6B6B6B',
    colorTextDescription: '#6B6B6B',

    colorBgLayout: SURFACE.canvas,
    colorBgContainer: SURFACE.card,
    colorBorderSecondary: SURFACE.border,

    // 形状：卡片 12 / 控件 8 / 小标签 6
    borderRadius: 8,
    borderRadiusLG: 12,
    borderRadiusSM: 6,
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
    },
    Menu: {
      // 选中文字单独压深：`colorPrimary` 压在浅色选中底上对比度不足
      itemSelectedColor: '#0958D9',
      itemSelectedBg: SURFACE.selected,
      itemBorderRadius: 8,
      itemHeight: 40,
      itemMarginInline: 8,
    },
    Table: {
      headerBg: SURFACE.subtle,
      headerSplitColor: 'transparent',
      rowHoverBg: '#F7F9FF',
      cellPaddingBlock: 10,
      borderColor: '#F0F1F3',
    },
    Card: { paddingLG: 16, borderRadiusLG: 12 },
    Button: { primaryShadow: 'none', defaultShadow: 'none' },
    Modal: { borderRadiusLG: 12 },
  },
}


/** 暗色表面：与 tokens.css 的 [data-theme='dark'] 一一对应 */
export const SURFACE_DARK = {
  canvas: '#0F1115',
  card: '#161A21',
  border: '#262B35',
  subtle: '#1B2029',
  selected: '#1E2A54',
} as const

/**
 * 暗色 theme config：只覆盖「会随底色反向变化」的 token。
 * 其余（圆角、控件高度、间距）亮暗共用，避免两套配置慢慢走偏。
 */
const darkThemeConfig: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: BRAND.primary,
    colorInfo: BRAND.primary,
    colorSuccess: '#3FB950',
    colorWarning: '#D29922',
    colorError: '#F85149',
    colorLink: '#6EA8FE',

    colorText: '#E6E9EF',
    colorTextSecondary: '#A6ADBB',
    colorTextTertiary: '#8B93A1',
    colorTextDescription: '#8B93A1',

    colorBgLayout: SURFACE_DARK.canvas,
    colorBgContainer: SURFACE_DARK.card,
    colorBgElevated: '#1B2029',
    colorBorderSecondary: SURFACE_DARK.border,
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
      itemSelectedColor: '#9DB4FF',
      itemSelectedBg: SURFACE_DARK.selected,
      itemBorderRadius: 8,
      itemHeight: 40,
      itemMarginInline: 8,
    },
    Table: {
      headerBg: SURFACE_DARK.subtle,
      headerSplitColor: 'transparent',
      rowHoverBg: '#1C2230',
      cellPaddingBlock: 10,
      borderColor: SURFACE_DARK.border,
    },
    Card: { paddingLG: 16, borderRadiusLG: 12 },
    Button: { primaryShadow: 'none', defaultShadow: 'none' },
    Modal: { borderRadiusLG: 12 },
  },
}

/**
 * 按模式取主题：亮色沿用原配置（**不改动**，避免视觉回归），暗色只覆盖必要 token。
 * 调用方传入 `resolveMode()` 的结果（'light' | 'dark'）。
 */
export function getThemeConfig(mode: 'light' | 'dark'): ThemeConfig {
  return mode === 'dark' ? darkThemeConfig : themeConfig
}
