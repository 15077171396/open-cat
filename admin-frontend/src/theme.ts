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
