# 管理后台 gi-demo 风格改版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `admin-frontend` 的布局、配色、组件与页面样式改成 gi-demo（Gi Admin Pro）风格：蓝色主色 + 橙色点缀、4px 圆角、浅灰画布 + 白色页面卡、顶栏面包屑、多标签页。

**Architecture:** 先改设计 token（`theme.ts` + `tokens.css`）驱动 antd 与手写 CSS，再重构布局外壳（`AdminLayout` + 新增 `PageTabs`），然后统一 7 个共享 UI 组件，最后清扫 24 个 section CSS 与页面内联色值，单独打磨登录页/首页看板/工作台/分析页。

**Tech Stack:** React 18、TypeScript 5.5、Vite 5、Ant Design 6、Zustand 4、Vitest 2、react-router-dom 6。

**Spec:** `docs/superpowers/specs/2026-10-31-admin-ui-gi-design.md`

> **执行状态（2026-10-31）：** 全部任务已完成。`npm test` 438 passed；`npm run build`、`npm run lint` 通过。亮/暗色、桌面/移动端、标签页关闭/刷新、编辑页标题、ECharts 移动端宽度均已核对。未创建 git 提交（等待用户确认）。

## Global Constraints

- 所有改动位于 `admin-frontend/`；**不改后端、接口、RBAC、路由表、菜单下发逻辑**。
- **不新增 npm 依赖**；不引入 Web 字体。
- 设计 token 必须两处同步：`src/theme.ts` 与 `src/styles/sections/tokens.css`；主色 `#165DFF`、画布 `#F5F7FD`、主边框 `#E5E6EB`、主文字 `#121314`、基础圆角 `4px`。
- 暗色主题由 `[data-theme='dark']` 与 `getThemeConfig('dark')` 同时驱动，不能只改一边。
- 保留：亮/暗/跟随系统、侧边/顶部布局、移动端抽屉、i18n、所有权限守卫与业务逻辑。
- antd 覆盖样式放在新文件 `src/styles/sections/antd-overrides.css`，在 `custom.css` 中排在 `theme-dark.css` 之后、`responsive.css` 之前；用双类名提高特异性。
- `responsive.css` 必须最后加载。
- 标签页 key 使用完整 `pathname`（含动态 id）；`sessionStorage` 键 `ai_cat_admin_tabs`；最多 10 个；首页 `/` 固定。
- 颜色/圆角禁止在新代码里写死十六进制与像素；语义色沿用现有 AA 取值：成功 `#16A34A`、警告 `#D97706`、危险 `#CF1322`。
- 命令均在 `admin-frontend/` 目录执行：`npm test`、`npm run build`、`npm run lint`。
- 提交步骤仅在用户明确授权提交时执行；不要 `--amend`、不要 `--no-verify`。

## File Structure

**新增**

- `src/styles/sections/layout.css`：顶栏、侧栏 logo、内容页面卡、标签页条样式。
- `src/styles/sections/antd-overrides.css`：antd 组件视觉覆盖（卡头橙条、表格、分页、菜单等）。
- `src/utils/pageTabs.ts`：标签页纯函数（增删、上限、持久化）。
- `src/utils/pageTabs.test.ts`：标签页纯函数测试。
- `src/utils/greeting.ts` + `.test.ts`：首页问候语时段判断。
- `src/store/pageTitleStore.ts` + `.test.ts`：页面标题注册表。
- `src/components/Layout/PageTabs.tsx` + `.test.tsx`：标签页组件。
- `src/styles/sections/login.css`：登录页左右分栏样式。
- `src/styles/tokens.test.ts`：token 同步守卫。

**修改**

- `src/theme.ts`、`src/styles/sections/tokens.css`、`src/styles/custom.css`、`src/styles/sections/base.css`
- `src/components/Layout/AdminLayout.tsx`
- `src/components/ui/PageHeader.tsx`、`ListToolbar.tsx`、`DataTable.tsx`、`StatusTag.tsx`、`ModalForm.tsx`
- `src/components/ListPageHeader.tsx`
- `src/components/settings/settingsKit.css`
- `src/styles/sections/adm-common.css`、`dashboard.css`、`workplace.css`、`analysis.css`、`shared-overrides.css`、`theme-dark.css`、`responsive.css` 及其余模块 section CSS
- `src/pages/Login.tsx`、`src/pages/Login.test.tsx`
- `src/pages/Dashboard.tsx`、`src/components/dashboard/*`、`src/components/analysis/*`
- `src/pages/Workplace/index.tsx`、`src/pages/Analysis.tsx`
- `src/i18n/messages.ts`
- `docs/07-开源文档/06-UI设计规范.md`

---

### Task 1: 设计 Token 与 antd 主题

**Files:**
- Modify: `src/theme.ts`
- Modify: `src/styles/sections/tokens.css`
- Create: `src/styles/sections/antd-overrides.css`
- Modify: `src/styles/sections/base.css`
- Modify: `src/styles/custom.css`
- Test: `src/styles/tokens.test.ts`

**Interfaces:**
- Produces: CSS 变量 `--app-primary` `#165dff`、`--app-accent` `#ff9a2e`、`--app-canvas` `#f5f7fd`、`--app-radius` `4px`、`--app-radius-lg` `8px`、`--app-radius-pill` `999px`、`--app-page-pad` `16px`、`--app-tabs-h` `32px`、`--app-table-hover`；`themeConfig` 与 `darkThemeConfig`。

- [ ] **Step 1: 写 token 同步守卫测试**

创建 `src/styles/tokens.test.ts`：

```ts
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const themeSource = readFileSync(resolve(here, '../theme.ts'), 'utf8').toLowerCase()
const tokenSource = readFileSync(resolve(here, 'sections/tokens.css'), 'utf8').toLowerCase()
const lightSource = tokenSource.split("[data-theme='dark']")[0]
const darkSource = tokenSource.split("[data-theme='dark']")[1] ?? ''

describe('tokens.css 与 theme.ts 同步', () => {
  it('亮色品牌/表面/文字 token 一致', () => {
    expect(themeSource).toContain('#165dff')
    expect(themeSource).toContain('#f5f7fd')
    expect(themeSource).toContain('#121314')
    expect(themeSource).toContain('#e5e6eb')
    expect(themeSource).toContain('borderradius: 4')

    expect(lightSource).toContain('--app-primary: #165dff')
    expect(lightSource).toContain('--app-canvas: #f5f7fd')
    expect(lightSource).toContain('--app-text: #121314')
    expect(lightSource).toContain('--app-border: #e5e6eb')
    expect(lightSource).toContain('--app-radius: 4px')
    expect(lightSource).toContain('--app-tabs-h: 32px')
  })

  it('暗色主色与画布独立覆盖', () => {
    expect(darkSource).toContain('--app-primary: #4080ff')
    expect(darkSource).toContain('--app-canvas: #0f1115')
  })

  it('不残留旧靛蓝主色', () => {
    expect(lightSource).not.toContain('--app-primary: #4263eb')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- tokens.test.ts`
Expected: FAIL，`--app-primary: #165dff` 不存在。

- [ ] **Step 3: 替换 `theme.ts` 的品牌与表面常量**

把 `BRAND` 与 `SURFACE` 替换为：

```ts
export const BRAND = {
  primary: '#165DFF',
  primaryHover: '#4080FF',
  primaryActive: '#0E42D2',
  primaryWeak: '#E8F3FF',
  accent: '#FF9A2E',
  gradient: 'linear-gradient(135deg, #165DFF 0%, #0E42D2 100%)',
} as const

export const SURFACE = {
  canvas: '#F5F7FD',
  card: '#FFFFFF',
  border: '#E5E6EB',
  borderLight: '#EBEEF5',
  subtle: '#FAFAFA',
  fill: '#F2F3F5',
  selected: '#F2F3F5',
} as const
```

把 `themeConfig` 整体替换为：

```ts
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
```

替换 `SURFACE_DARK`：

```ts
export const SURFACE_DARK = {
  canvas: '#0F1115',
  card: '#161A21',
  border: '#262B35',
  borderLight: '#20252E',
  subtle: '#1B2029',
  fill: '#21262F',
  selected: '#1E2A54',
} as const
```

把 `darkThemeConfig` 整体替换为：

```ts
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
```

- [ ] **Step 4: 重写 `tokens.css` 变量**

把 `:root` 与 `[data-theme='dark']` 两个块整体替换为：

```css
:root {
  /* 品牌 */
  --app-primary: #165dff;
  --app-primary-hover: #4080ff;
  --app-primary-active: #0e42d2;
  --app-primary-weak: #e8f3ff;
  --app-primary-border: #bedaff;
  --app-accent: #ff9a2e;
  --app-accent-bg: #fff5e8;

  /* 表面 */
  --app-canvas: #f5f7fd;
  --app-card: #ffffff;
  --app-elevated: #ffffff;
  --app-subtle: #fafafa;
  --app-fill: #f2f3f5;
  --app-fill-strong: #e5e6eb;
  --app-border: #e5e6eb;
  --app-border-light: #ebeef5;
  --app-border-strong: #dcdfe6;
  --app-table-hover: #f7f9fc;

  /* 文字 */
  --app-text: #121314;
  --app-text-2: #5c5c5c;
  --app-text-3: #909399;
  --app-text-4: #c2c2c2;

  /* 语义 */
  --app-success: #16a34a;
  --app-success-bg: #eaf7ee;
  --app-warning: #d97706;
  --app-warning-bg: #fff7e8;
  --app-danger: #cf1322;
  --app-danger-bg: #fdecec;
  --app-danger-border: #ffccc7;
  --app-neutral: #5c5c5c;
  --app-neutral-bg: #f2f3f5;
  --app-info-bg: #e8f3ff;
  --app-purple: #722ed1;
  --app-purple-bg: #f4f0ff;
  --app-gold-bg: #fff8e6;
  --app-link: #165dff;
  --app-tint: #eef4fb;
  --app-tint-soft: #f8fbff;
  --app-tint-border: #dbe8f7;
  --app-mask: rgba(18, 19, 20, 0.45);

  /* 形状与节拍 */
  --app-radius: 4px;
  --app-radius-sm: 4px;
  --app-radius-xs: 4px;
  --app-radius-lg: 8px;
  --app-radius-pill: 999px;
  --app-shadow: none;
  --app-shadow-float: 0 6px 16px rgba(18, 19, 20, 0.08);
  --app-gap: 16px;
  --app-page-pad: 16px;
  --app-header-h: 56px;
  --app-tabs-h: 32px;
}

[data-theme='dark'] {
  --app-primary: #4080ff;
  --app-primary-hover: #6aa1ff;
  --app-primary-active: #165dff;
  --app-primary-weak: #1e2a54;
  --app-primary-border: #2d3a6b;
  --app-accent: #ffb65c;
  --app-accent-bg: #2b2313;

  --app-canvas: #0f1115;
  --app-card: #161a21;
  --app-elevated: #1b2029;
  --app-subtle: #1b2029;
  --app-fill: #21262f;
  --app-fill-strong: #2a303a;
  --app-border: #262b35;
  --app-border-light: #20252e;
  --app-border-strong: #3a4250;
  --app-table-hover: #21262f;

  --app-text: #e6e9ef;
  --app-text-2: #a6adbb;
  --app-text-3: #8b93a1;
  --app-text-4: #5f6672;

  --app-success: #3fb950;
  --app-success-bg: #14291b;
  --app-warning: #d29922;
  --app-warning-bg: #2b2313;
  --app-danger: #f85149;
  --app-danger-bg: #2c1a1c;
  --app-danger-border: #5c2b2b;
  --app-neutral: #a6adbb;
  --app-neutral-bg: #21262f;
  --app-info-bg: #14243b;
  --app-purple: #b37feb;
  --app-purple-bg: #221c35;
  --app-gold-bg: #2b2515;
  --app-link: #6ea8fe;
  --app-tint: #1a2532;
  --app-tint-soft: #1e2937;
  --app-tint-border: #2a3a4f;
  --app-mask: rgba(0, 0, 0, 0.55);
}
```

- [ ] **Step 5: 新建 antd 覆盖样式并登记加载顺序**

创建 `src/styles/sections/antd-overrides.css`：

```css
/* antd 组件视觉对齐 gi-demo：卡头橙条、扁平卡、表格密度、实心分页 */
.ant-card.ant-card {
  border-color: var(--app-border-light);
  box-shadow: none;
}

.ant-card .ant-card-head {
  min-height: 44px;
  padding: 0 16px;
  border-bottom: none;
}

.ant-card .ant-card-head .ant-card-head-title {
  position: relative;
  padding-left: 12px;
  font-size: 16px;
  font-weight: 500;
  color: var(--app-text);
}

.ant-card .ant-card-head .ant-card-head-title::before {
  position: absolute;
  top: 50%;
  left: 0;
  width: 4px;
  height: 16px;
  content: '';
  background: var(--app-accent);
  border-radius: 0 var(--app-radius) var(--app-radius) 0;
  transform: translateY(-50%);
}

.ant-table-wrapper .ant-table-thead > tr > th {
  font-size: 13px;
  font-weight: 600;
  border-bottom: 1px solid var(--app-border-light);
}

.ant-table-wrapper .ant-table-tbody > tr > td {
  border-bottom: 1px solid var(--app-border-light);
}

.ant-table-wrapper .ant-table-tbody > tr.ant-table-row:hover > td {
  background: var(--app-table-hover);
}

.ant-pagination .ant-pagination-item {
  border-radius: var(--app-radius);
}

.ant-pagination .ant-pagination-item-active {
  background: var(--app-primary);
  border-color: var(--app-primary);
}

.ant-pagination .ant-pagination-item-active a {
  color: #fff;
}

.ant-menu-light .ant-menu-item-selected {
  font-weight: 500;
}

.ant-menu-light .ant-menu-item-group-title {
  font-size: 12px;
  color: var(--app-text-3);
}

.ant-tabs .ant-tabs-tab.ant-tabs-tab-active .ant-tabs-tab-btn {
  color: var(--app-primary);
}

.ant-modal .ant-modal-content {
  border-radius: var(--app-radius-lg);
}

.ant-drawer .ant-drawer-header {
  min-height: 56px;
  padding: 0 16px;
}

.ant-tag.ant-tag {
  border-radius: var(--app-radius);
}
```

在 `src/styles/custom.css` 中，`theme-dark.css` 之后、`responsive.css` 之前加入：

```css
/* antd 组件视觉对齐 gi-demo（须在 responsive 之前） */
@import './sections/antd-overrides.css';
```

- [ ] **Step 6: 更新 base.css 字体与 spinner**

把 `body` 的 `font-family` 改为：

```css
font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
  'Hiragino Sans GB', 'Microsoft YaHei', 'Source Han Sans SC', 'Noto Sans SC',
  'Noto Sans CJK SC', sans-serif;
```

把 `.page-loading-spinner` 的 `border-top-color` 改为 `var(--app-primary)`；把 `.error-boundary-retry` 的背景改为 `var(--app-primary)`、hover 改为 `var(--app-primary-hover)`、圆角改为 `var(--app-radius)`。

- [ ] **Step 7: 运行测试与构建**

Run: `npm test -- tokens.test.ts`
Expected: PASS（3 条）。

Run: `npm run build`
Expected: 构建成功，无 TS 错误。

- [ ] **Step 8: 提交（仅在获得提交授权时）**

```bash
git add src/theme.ts src/styles/sections/tokens.css src/styles/sections/antd-overrides.css src/styles/sections/base.css src/styles/custom.css src/styles/tokens.test.ts
git commit -m "feat(admin-ui): 切换 gi-demo 设计 token 与 antd 主题"
```

---

### Task 2: 菜单标题、页面标题注册

**Files:**
- Modify: `src/utils/menu.ts`
- Modify: `src/utils/menu.test.ts`
- Create: `src/store/pageTitleStore.ts`
- Create: `src/store/pageTitleStore.test.ts`
- Modify: `src/components/ui/PageHeader.tsx`
- Modify: `src/components/ui/ui.test.tsx`

**Interfaces:**
- Produces: `collectMenuLabels(tree: MenuTree): Map<string, string>`；`usePageTitleStore`（`titles: Record<string, string>`、`setTitle(path, title)`、`clearTitle(path)`、`reset()`）；`PageHeader` 自动把 `title` 注册到 `useLocation().pathname`。

- [ ] **Step 1: 写 `collectMenuLabels` 失败测试**

在 `src/utils/menu.test.ts` 末尾追加：

```ts
describe('collectMenuLabels', () => {
  it('把菜单叶子 key 映射到当前语言标题', () => {
    const labels = collectMenuLabels(tree)
    expect(labels.get('/tutorials')).toBe('教程管理')
    expect(labels.get('/')).toBe('仪表盘')
    expect(labels.get('/not-exist')).toBeUndefined()
  })
})
```

同时把 `collectMenuLabels` 加进文件顶部的 import。

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- menu.test.ts`
Expected: FAIL，`collectMenuLabels is not a function`。

- [ ] **Step 3: 实现 `collectMenuLabels`**

在 `src/utils/menu.ts` 追加：

```ts
/** 菜单叶子 key → 当前语言标题（标签页标题的兜底来源） */
export function collectMenuLabels(tree: MenuTree): Map<string, string> {
  const labels = new Map<string, string>()
  for (const leaf of flattenLeaves(tree)) labels.set(leaf.key, leaf.label)
  return labels
}
```

- [ ] **Step 4: 写页面标题 store 测试**

创建 `src/store/pageTitleStore.test.ts`：

```ts
import { beforeEach, describe, expect, it } from 'vitest'
import { usePageTitleStore } from './pageTitleStore'

describe('pageTitleStore', () => {
  beforeEach(() => usePageTitleStore.getState().reset())

  it('按 pathname 写入标题', () => {
    usePageTitleStore.getState().setTitle('/users', '用户管理')
    expect(usePageTitleStore.getState().titles['/users']).toBe('用户管理')
  })

  it('改为相同标题时不产生新对象', () => {
    const store = usePageTitleStore.getState()
    store.setTitle('/users', '用户管理')
    const first = usePageTitleStore.getState().titles
    store.setTitle('/users', '用户管理')
    expect(usePageTitleStore.getState().titles).toBe(first)
  })

  it('clearTitle 只删指定路径，reset 清空', () => {
    const store = usePageTitleStore.getState()
    store.setTitle('/users', '用户管理')
    store.setTitle('/roles', '角色管理')
    store.clearTitle('/users')
    expect(usePageTitleStore.getState().titles).toEqual({ '/roles': '角色管理' })
    store.reset()
    expect(usePageTitleStore.getState().titles).toEqual({})
  })
})
```

- [ ] **Step 5: 实现 `pageTitleStore.ts`**

```ts
import { create } from 'zustand'

interface PageTitleState {
  titles: Record<string, string>
  setTitle: (pathname: string, title: string) => void
  clearTitle: (pathname: string) => void
  reset: () => void
}

/** 页面标题注册表：PageHeader 写入，PageTabs 读取。不持久化，随页面会话生效。 */
export const usePageTitleStore = create<PageTitleState>((set) => ({
  titles: {},
  setTitle: (pathname, title) =>
    set((state) => {
      if (!pathname || !title || state.titles[pathname] === title) return state
      return { titles: { ...state.titles, [pathname]: title } }
    }),
  clearTitle: (pathname) =>
    set((state) => {
      if (!(pathname in state.titles)) return state
      const next = { ...state.titles }
      delete next[pathname]
      return { titles: next }
    }),
  reset: () => set({ titles: {} }),
}))
```

- [ ] **Step 6: PageHeader 注册标题**

在 `PageHeader.tsx` 顶部加：

```ts
import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { usePageTitleStore } from '../../store/pageTitleStore'
```

在组件体内加：

```ts
const location = useLocation()
const setTitle = usePageTitleStore((s) => s.setTitle)

useEffect(() => {
  setTitle(location.pathname, title)
}, [location.pathname, title, setTitle])
```

- [ ] **Step 7: 更新页头测试并加注册断言**

在 `ui.test.tsx` 顶部加 `waitFor` 与 `MemoryRouter`、`usePageTitleStore` 的 import。把两个 `PageHeader` 用例的 `render(...)` 包进 `<MemoryRouter initialEntries={['/users']}>`。追加：

```tsx
it('把标题注册到标签页标题表', async () => {
  usePageTitleStore.getState().reset()
  render(
    <MemoryRouter initialEntries={['/users']}>
      <PageHeader icon={<span>i</span>} title="用户管理" />
    </MemoryRouter>,
  )
  await waitFor(() => {
    expect(usePageTitleStore.getState().titles['/users']).toBe('用户管理')
  })
})
```

- [ ] **Step 8: 运行测试**

Run: `npm test -- menu.test.ts pageTitleStore.test.ts ui.test.tsx`
Expected: 全部 PASS。

- [ ] **Step 9: 提交（仅在获得提交授权时）**

```bash
git add src/utils/menu.ts src/utils/menu.test.ts src/store/pageTitleStore.ts src/store/pageTitleStore.test.ts src/components/ui/PageHeader.tsx src/components/ui/ui.test.tsx
git commit -m "feat(admin-ui): 增加页面标题注册与菜单标题映射"
```

---

### Task 3: 标签页纯函数

**Files:**
- Create: `src/utils/pageTabs.ts`
- Create: `src/utils/pageTabs.test.ts`

**Interfaces:**
- Produces: `HOME_TAB = '/'`、`MAX_TABS = 10`、`normalizeTabs`、`addTab`、`limitTabs`、`removeTab`、`nextTabAfterClose`、`closeOtherTabs`、`closeAllTabs`、`readTabs`、`writeTabs`。

- [ ] **Step 1: 写测试**

创建 `src/utils/pageTabs.test.ts`：

```ts
import { describe, expect, it } from 'vitest'
import {
  HOME_TAB,
  MAX_TABS,
  addTab,
  closeAllTabs,
  closeOtherTabs,
  limitTabs,
  nextTabAfterClose,
  normalizeTabs,
  readTabs,
  removeTab,
  writeTabs,
} from './pageTabs'

function memoryStorage() {
  let value: string | null = null
  return {
    getItem: () => value,
    setItem: (_key: string, next: string) => {
      value = next
    },
  }
}

describe('pageTabs', () => {
  it('首页固定在最前且去重', () => {
    expect(normalizeTabs(['/users', '/users', '/roles'])).toEqual([HOME_TAB, '/users', '/roles'])
    expect(normalizeTabs([HOME_TAB, '/users'])).toEqual([HOME_TAB, '/users'])
  })

  it('addTab 不重复添加，超过上限淘汰最旧的非当前页', () => {
    expect(addTab([HOME_TAB, '/users'], '/users')).toEqual([HOME_TAB, '/users'])
    const many = [HOME_TAB, ...Array.from({ length: MAX_TABS }, (_, i) => `/p${i}`)]
    const next = addTab(many, '/new')
    expect(next).toContain('/new')
    expect(next).toContain(HOME_TAB)
    expect(next.length).toBe(MAX_TABS)
    expect(next).not.toContain('/p0')
  })

  it('removeTab 不允许移除首页', () => {
    expect(removeTab([HOME_TAB, '/users'], HOME_TAB)).toEqual([HOME_TAB, '/users'])
    expect(removeTab([HOME_TAB, '/users'], '/users')).toEqual([HOME_TAB])
  })

  it('关闭激活标签回到左侧相邻标签，关闭非激活标签不跳转', () => {
    const tabs = [HOME_TAB, '/users', '/roles']
    expect(nextTabAfterClose(tabs, '/roles', '/roles')).toBe('/users')
    expect(nextTabAfterClose(tabs, '/users', '/users')).toBe(HOME_TAB)
    expect(nextTabAfterClose(tabs, '/roles', '/users')).toBeNull()
  })

  it('关闭其他 / 关闭全部都保留首页', () => {
    expect(closeOtherTabs([HOME_TAB, '/users', '/roles'], '/roles')).toEqual([HOME_TAB, '/roles'])
    expect(closeAllTabs()).toEqual([HOME_TAB])
  })

  it('limitTabs 保留当前页与首页', () => {
    const many = [HOME_TAB, ...Array.from({ length: MAX_TABS }, (_, i) => `/p${i}`)]
    const next = limitTabs(many, '/p0')
    expect(next).toContain('/p0')
    expect(next).toContain(HOME_TAB)
    expect(next.length).toBe(MAX_TABS)
  })

  it('读写 sessionStorage；损坏或版本不符时回到首页', () => {
    const storage = memoryStorage()
    writeTabs(storage, ['/users', '/roles'])
    expect(readTabs(storage)).toEqual([HOME_TAB, '/users', '/roles'])

    storage.setItem('x', '{bad json')
    expect(readTabs(storage)).toEqual([HOME_TAB])

    storage.setItem('x', JSON.stringify({ v: 0, tabs: ['/users'] }))
    expect(readTabs(storage)).toEqual([HOME_TAB])
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- pageTabs.test.ts`
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现 `pageTabs.ts`**

```ts
export const HOME_TAB = '/'
export const MAX_TABS = 10
const STORAGE_KEY = 'ai_cat_admin_tabs'
const STORAGE_VERSION = 1

interface TabSnapshot {
  v?: number
  tabs?: unknown
}

export function normalizeTabs(tabs: string[]): string[] {
  const unique: string[] = []
  for (const key of tabs) {
    if (typeof key !== 'string' || !key.startsWith('/')) continue
    if (!unique.includes(key)) unique.push(key)
  }
  if (!unique.includes(HOME_TAB)) unique.unshift(HOME_TAB)
  return unique
}

export function limitTabs(tabs: string[], activeKey: string): string[] {
  const normalized = normalizeTabs(tabs)
  if (normalized.length <= MAX_TABS) return normalized
  const rest = normalized.filter((key) => key !== HOME_TAB)
  const tail = rest.slice(-(MAX_TABS - 1))
  if (activeKey !== HOME_TAB && !tail.includes(activeKey)) {
    tail.shift()
    tail.push(activeKey)
  }
  return [HOME_TAB, ...tail]
}

export function addTab(tabs: string[], key: string): string[] {
  const normalized = normalizeTabs(tabs)
  if (normalized.includes(key)) return normalized
  return limitTabs([...normalized, key], key)
}

export function removeTab(tabs: string[], key: string): string[] {
  if (key === HOME_TAB) return normalizeTabs(tabs)
  return normalizeTabs(tabs.filter((item) => item !== key))
}

export function nextTabAfterClose(
  tabs: string[],
  key: string,
  activeKey: string,
): string | null {
  if (key !== activeKey) return null
  const remaining = removeTab(tabs, key)
  const index = tabs.indexOf(key)
  const left = remaining[Math.min(Math.max(index - 1, 0), remaining.length - 1)]
  return left ?? HOME_TAB
}

export function closeOtherTabs(tabs: string[], key: string): string[] {
  return normalizeTabs(tabs.filter((item) => item === HOME_TAB || item === key))
}

export function closeAllTabs(): string[] {
  return [HOME_TAB]
}

export function readTabs(storage: Pick<Storage, 'getItem'>): string[] {
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return [HOME_TAB]
    const parsed = JSON.parse(raw) as TabSnapshot
    if (parsed?.v !== STORAGE_VERSION || !Array.isArray(parsed.tabs)) return [HOME_TAB]
    return normalizeTabs(parsed.tabs.filter((key): key is string => typeof key === 'string'))
  } catch {
    return [HOME_TAB]
  }
}

export function writeTabs(storage: Pick<Storage, 'setItem'>, tabs: string[]): void {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify({ v: STORAGE_VERSION, tabs: normalizeTabs(tabs) }))
  } catch {
    // 隐私模式 / 配额满：标签页只是便利功能，写不进去不影响主流程
  }
}
```

- [ ] **Step 4: 运行测试**

Run: `npm test -- pageTabs.test.ts`
Expected: 7 条全部 PASS。

- [ ] **Step 5: 提交（仅在获得提交授权时）**

```bash
git add src/utils/pageTabs.ts src/utils/pageTabs.test.ts
git commit -m "feat(admin-ui): 增加多标签页纯函数与持久化"
```

---

### Task 4: PageTabs 组件与布局样式

**Files:**
- Create: `src/components/Layout/PageTabs.tsx`
- Create: `src/components/Layout/PageTabs.test.tsx`
- Create: `src/styles/sections/layout.css`
- Modify: `src/styles/custom.css`
- Modify: `src/i18n/messages.ts`

**Interfaces:**
- Consumes: `HOME_TAB`；`useI18n`。
- Produces: `<PageTabs tabs activeKey getTitle onChange onClose onCloseOthers onCloseAll onRefresh />`；i18n 键 `tabs.*`。

- [ ] **Step 1: 增加 i18n 文案**

在 `src/i18n/messages.ts` 的中文与英文对象中分别加入：

```ts
'tabs.label': '页签',
'tabs.refresh': '刷新当前页',
'tabs.more': '标签页操作',
'tabs.close': '关闭 {title}',
'tabs.closeOthers': '关闭其他',
'tabs.closeAll': '关闭全部',
'tabs.untitled': '未命名页面',
```

```ts
'tabs.label': 'Tabs',
'tabs.refresh': 'Refresh page',
'tabs.more': 'Tab actions',
'tabs.close': 'Close {title}',
'tabs.closeOthers': 'Close others',
'tabs.closeAll': 'Close all',
'tabs.untitled': 'Untitled page',
```

- [ ] **Step 2: 写组件测试**

创建 `src/components/Layout/PageTabs.test.tsx`：

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import PageTabs from './PageTabs'

const titles: Record<string, string> = { '/': '首页', '/users': '用户管理' }

function renderTabs(overrides: Partial<React.ComponentProps<typeof PageTabs>> = {}) {
  const props = {
    tabs: ['/', '/users'],
    activeKey: '/users',
    getTitle: (key: string) => titles[key] ?? key,
    onChange: vi.fn(),
    onClose: vi.fn(),
    onCloseOthers: vi.fn(),
    onCloseAll: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  }
  render(<PageTabs {...props} />)
  return props
}

describe('PageTabs', () => {
  it('渲染全部标签并标记当前项', () => {
    renderTabs()
    const active = screen.getByRole('tab', { name: /用户管理/ })
    expect(active).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /首页/ })).toHaveAttribute('aria-selected', 'false')
  })

  it('点击标签切换路由', async () => {
    const props = renderTabs()
    await userEvent.click(screen.getByRole('button', { name: '首页' }))
    expect(props.onChange).toHaveBeenCalledWith('/')
  })

  it('点击关闭只触发关闭，不触发切换', async () => {
    const props = renderTabs()
    await userEvent.click(screen.getByRole('button', { name: '关闭 用户管理' }))
    expect(props.onClose).toHaveBeenCalledWith('/users')
    expect(props.onChange).not.toHaveBeenCalled()
  })

  it('首页不可关闭', () => {
    renderTabs()
    expect(screen.queryByRole('button', { name: '关闭 首页' })).not.toBeInTheDocument()
  })

  it('刷新按钮触发 onRefresh', async () => {
    const props = renderTabs()
    await userEvent.click(screen.getByRole('button', { name: '刷新当前页' }))
    expect(props.onRefresh).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 3: 运行测试确认失败**

Run: `npm test -- PageTabs.test.tsx`
Expected: FAIL，组件不存在。

- [ ] **Step 4: 实现 `PageTabs.tsx`**

```tsx
import { Button, Dropdown, Space, Tooltip } from 'antd'
import { CloseOutlined, MoreOutlined, ReloadOutlined } from '@ant-design/icons'
import { HOME_TAB } from '../../utils/pageTabs'
import { useI18n } from '../../i18n'

export interface PageTabsProps {
  tabs: string[]
  activeKey: string
  getTitle: (key: string) => string
  onChange: (key: string) => void
  onClose: (key: string) => void
  onCloseOthers: (key: string) => void
  onCloseAll: () => void
  onRefresh: () => void
}

export default function PageTabs({
  tabs,
  activeKey,
  getTitle,
  onChange,
  onClose,
  onCloseOthers,
  onCloseAll,
  onRefresh,
}: PageTabsProps) {
  const { t } = useI18n()

  return (
    <div className="page-tabs">
      <div className="page-tabs__list" role="tablist" aria-label={t('tabs.label')}>
        {tabs.map((key) => {
          const title = getTitle(key)
          const active = key === activeKey
          return (
            <div
              key={key}
              className={`page-tabs__item${active ? ' is-active' : ''}`}
              role="tab"
              aria-selected={active}
            >
              <button type="button" className="page-tabs__label" onClick={() => onChange(key)}>
                {title}
              </button>
              {key !== HOME_TAB && (
                <button
                  type="button"
                  className="page-tabs__close"
                  aria-label={t('tabs.close', { title })}
                  onClick={(event) => {
                    event.stopPropagation()
                    onClose(key)
                  }}
                >
                  <CloseOutlined />
                </button>
              )}
            </div>
          )
        })}
      </div>
      <Space size={2} className="page-tabs__actions">
        <Tooltip title={t('tabs.refresh')}>
          <Button
            type="text"
            size="small"
            icon={<ReloadOutlined />}
            aria-label={t('tabs.refresh')}
            onClick={onRefresh}
          />
        </Tooltip>
        <Dropdown
          placement="bottomRight"
          menu={{
            items: [
              { key: 'others', label: t('tabs.closeOthers'), onClick: () => onCloseOthers(activeKey) },
              { key: 'all', label: t('tabs.closeAll'), onClick: onCloseAll },
            ],
          }}
        >
          <Button
            type="text"
            size="small"
            icon={<MoreOutlined />}
            aria-label={t('tabs.more')}
          />
        </Dropdown>
      </Space>
    </div>
  )
}
```

- [ ] **Step 5: 创建 layout.css 并登记**

创建 `src/styles/sections/layout.css`：

```css
/* 布局外壳：内容页面卡、顶栏、侧栏 logo、多标签页 */
.app-content {
  flex: 1;
  overflow: auto;
  margin: 16px;
  padding: var(--app-page-pad);
  background: var(--app-card);
  border-radius: var(--app-radius);
}

.app-content--canvas {
  margin: 0;
  padding: var(--app-page-pad);
  background: transparent;
  border-radius: 0;
}

.app-content--edit {
  padding-top: 0;
  padding-bottom: 0;
}

.app-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  padding: 0 16px;
  overflow: hidden;
}

.app-logo.is-collapsed {
  justify-content: center;
  padding: 0;
}

.app-logo__img {
  flex: none;
  width: 28px;
  height: 28px;
  object-fit: cover;
  border-radius: 6px;
}

.app-logo__text {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
  white-space: nowrap;
}

.page-tabs {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  height: var(--app-tabs-h);
  padding: 0 12px;
  background: var(--app-card);
  border-bottom: 1px solid var(--app-border-light);
}

.page-tabs__list {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 2px;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.page-tabs__list::-webkit-scrollbar {
  display: none;
}

.page-tabs__item {
  display: flex;
  flex: none;
  align-items: center;
  height: 24px;
  padding: 0 4px 0 8px;
  font-size: 13px;
  color: var(--app-text-2);
  white-space: nowrap;
  border-radius: var(--app-radius);
}

.page-tabs__item:hover {
  background: var(--app-fill);
}

.page-tabs__item.is-active {
  color: var(--app-primary);
}

.page-tabs__label {
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
  background: transparent;
  border: 0;
}

.page-tabs__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-left: 2px;
  font-size: 10px;
  color: var(--app-text-3);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: var(--app-radius);
  opacity: 0;
}

.page-tabs__item:hover .page-tabs__close,
.page-tabs__item.is-active .page-tabs__close,
.page-tabs__close:focus-visible {
  opacity: 1;
}

.page-tabs__close:hover {
  color: var(--app-danger);
  background: var(--app-danger-bg);
}

.page-tabs__actions {
  flex: none;
}
```

在 `custom.css` 的 `base.css` 之后加入：

```css
/* 布局外壳：顶栏、侧栏 logo、内容页面卡、多标签页 */
@import './sections/layout.css';
```

- [ ] **Step 6: 运行测试**

Run: `npm test -- PageTabs.test.tsx`
Expected: 5 条 PASS。

- [ ] **Step 7: 提交（仅在获得提交授权时）**

```bash
git add src/components/Layout/PageTabs.tsx src/components/Layout/PageTabs.test.tsx src/styles/sections/layout.css src/styles/custom.css src/i18n/messages.ts
git commit -m "feat(admin-ui): 新增多标签页组件与布局样式"
```

---

### Task 5: AdminLayout 接入标签页、页面卡与品牌 logo

**Files:**
- Modify: `src/components/Layout/AdminLayout.tsx`
- Modify: `src/styles/sections/responsive.css`

**Interfaces:**
- Consumes: `PageTabs`、`usePageTitleStore`、`collectMenuLabels`、`readTabs/writeTabs/addTab/removeTab/nextTabAfterClose/closeOtherTabs/closeAllTabs/HOME_TAB`。
- Produces: 路由切换自动加标签、关闭/刷新/关闭其他/全部；内容区按路由切换 `app-content--canvas` / `app-content--edit`。

- [ ] **Step 1: 增加 import**

在 `AdminLayout.tsx` 顶部补充：

```ts
import { useCallback, ... } from 'react' // 把 useCallback 合入现有 react import
import PageTabs from './PageTabs'
import { usePageTitleStore } from '../../store/pageTitleStore'
import {
  HOME_TAB,
  addTab,
  closeAllTabs,
  closeOtherTabs,
  nextTabAfterClose,
  readTabs,
  removeTab,
  writeTabs,
} from '../../utils/pageTabs'
```

并把 `collectMenuLabels` 加入现有 `../../utils/menu` import。

- [ ] **Step 2: 删除旧的 Content 内联尺寸常量**

删除 `contentStyle` 与 `contentStyleZeroVerticalPadding` 两个常量。`siderStyle` 改为：

```ts
const siderStyle: CSSProperties = {
  background: 'var(--app-card)',
  borderRight: 'none',
  boxShadow: '0 2px 5px rgba(0, 0, 0, 0.08)',
  position: 'fixed',
  left: 0,
  top: 0,
  height: '100vh',
  zIndex: 10,
  display: 'flex',
  flexDirection: 'column',
}
```

`headerStyle` 的 `borderBottom` 改为 `'1px solid var(--app-border-light)'`，并加 `className="app-header"`。删除 `logoStyle`（改用 `.app-logo`）。

- [ ] **Step 3: 增加标签页状态与处理函数**

在 `AdminLayout` 组件内、`isEditPage` 之后加入：

```ts
const isCanvasPage = location.pathname === '/' || location.pathname === '/workplace' || location.pathname === '/analysis'
const pageTitles = usePageTitleStore((s) => s.titles)
const clearTitle = usePageTitleStore((s) => s.clearTitle)
const menuLabels = useMemo(() => collectMenuLabels(visibleTree), [visibleTree])
const [tabs, setTabs] = useState<string[]>(() =>
  typeof window === 'undefined' ? [HOME_TAB] : readTabs(window.sessionStorage),
)
const [refreshKey, setRefreshKey] = useState(0)

useEffect(() => {
  setTabs((prev) => {
    const next = addTab(prev, location.pathname)
    if (next.length === prev.length && next.every((key, index) => key === prev[index])) return prev
    writeTabs(window.sessionStorage, next)
    return next
  })
}, [location.pathname])

useEffect(() => {
  if (!menuTree) return
  setTabs((prev) => {
    const menuKeys = [...menuLabels.keys()]
    const known = (key: string) =>
      key === HOME_TAB ||
      menuLabels.has(key) ||
      menuKeys.some((menuKey) => menuKey !== HOME_TAB && key.startsWith(`${menuKey}/`)) ||
      !!usePageTitleStore.getState().titles[key]
    const next = prev.filter(known)
    if (next.length === prev.length) return prev
    writeTabs(window.sessionStorage, next)
    return next
  })
}, [menuLabels, menuTree])

const tabTitle = useCallback(
  (key: string) =>
    pageTitles[key] ??
    menuLabels.get(key) ??
    menuLabels.get(resolveSelectedKey(visibleTree, key)) ??
    t('tabs.untitled'),
  [pageTitles, menuLabels, visibleTree, t],
)

const handleCloseTab = (key: string) => {
  const nextActive = nextTabAfterClose(tabs, key, location.pathname)
  const next = removeTab(tabs, key)
  setTabs(next)
  writeTabs(window.sessionStorage, next)
  clearTitle(key)
  if (key === location.pathname && nextActive) navigate(nextActive)
}

const handleCloseOthers = (key: string) => {
  const next = closeOtherTabs(tabs, key)
  tabs.filter((item) => !next.includes(item)).forEach(clearTitle)
  setTabs(next)
  writeTabs(window.sessionStorage, next)
}

const handleCloseAll = () => {
  tabs.filter((item) => item !== HOME_TAB).forEach(clearTitle)
  const next = closeAllTabs()
  setTabs(next)
  writeTabs(window.sessionStorage, next)
  navigate(HOME_TAB)
}

const handleRefreshTab = () => setRefreshKey((value) => value + 1)
```

- [ ] **Step 4: 替换 logo 与顶栏元素**

侧栏 logo 改为：

```tsx
<div className={`app-logo${collapsed ? ' is-collapsed' : ''}`}>
  <img className="app-logo__img" src={`${import.meta.env.BASE_URL}favicon-64.png`} alt="" />
  {!collapsed && <span className="app-logo__text">{t('layout.brand')}</span>}
</div>
```

顶部布局的 logo 改为同样结构（不带 `is-collapsed`）。删除 `BRAND` import 与两处 `Typography.Title` logo。

- [ ] **Step 5: 插入标签页并替换 Content**

在 `<Header>` 之后插入：

```tsx
{!isMobile && (
  <PageTabs
    tabs={tabs}
    activeKey={location.pathname}
    getTitle={tabTitle}
    onChange={(key) => navigate(key)}
    onClose={handleCloseTab}
    onCloseOthers={handleCloseOthers}
    onCloseAll={handleCloseAll}
    onRefresh={handleRefreshTab}
  />
)}
```

Content 改为：

```tsx
<Content
  className={[
    'app-content',
    isCanvasPage ? 'app-content--canvas' : '',
    isEditPage ? 'app-content--edit' : '',
  ]
    .filter(Boolean)
    .join(' ')}
>
  <RouteErrorBoundary key={`${location.pathname}:${refreshKey}`} path={location.pathname}>
    <Outlet />
  </RouteErrorBoundary>
</Content>
```

- [ ] **Step 6: 响应式补丁**

在 `responsive.css` 的移动端媒体查询内追加：

```css
.app-content {
  margin: 0;
  padding: 12px;
  --app-page-pad: 12px;
  border-radius: 0;
}

.app-content--edit {
  padding-top: 0;
  padding-bottom: 0;
}
```

- [ ] **Step 7: 构建与手工验证**

Run: `npm run build`
Expected: 成功。

启动开发服务并在浏览器检查：
- 打开两个菜单页，顶部出现标签页且当前项蓝色；
- 关闭当前标签回到左侧标签；关闭其他/全部只留首页；
- 刷新按钮重新挂载当前页；
- 浏览器刷新后标签仍在；
- 首页/工作台/分析为画布直铺，其余页面是白色页面卡；
- 编辑页顶部无多余留白。

- [ ] **Step 8: 提交（仅在获得提交授权时）**

```bash
git add src/components/Layout/AdminLayout.tsx src/styles/sections/responsive.css
git commit -m "feat(admin-ui): 布局外壳接入标签页、页面卡与品牌 logo"
```

---

### Task 6: 共享页头、工具条与表格扁平化

**Files:**
- Modify: `src/components/ListPageHeader.tsx`
- Modify: `src/components/ui/ListToolbar.tsx`
- Modify: `src/components/ui/DataTable.tsx`
- Modify: `src/styles/sections/adm-common.css`

**Interfaces:**
- Produces: `ListPageHeader` 复用 `PageHeader`；`ListToolbar` 与 `DataTable` 不再输出 Card 外壳；`.adm-header` 作为页面卡全宽卡头。

- [ ] **Step 1: 让 ListPageHeader 复用 PageHeader**

把 `src/components/ListPageHeader.tsx` 整体替换为：

```tsx
import type { ReactNode } from 'react'
import { Button } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import PageHeader from './ui/PageHeader'

interface ListPageHeaderProps {
  icon: ReactNode
  title: string
  onCreate?: () => void
  createLabel?: string
  extra?: ReactNode
}

export default function ListPageHeader({
  icon,
  title,
  onCreate,
  createLabel = '新建',
  extra,
}: ListPageHeaderProps) {
  return (
    <PageHeader
      icon={icon}
      title={title}
      actions={
        (extra || onCreate) && (
          <>
            {extra}
            {onCreate && (
              <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
                {createLabel}
              </Button>
            )}
          </>
        )
      }
    />
  )
}
```

- [ ] **Step 2: ListToolbar 去掉 Card 外壳**

把 `ListToolbar.tsx` 的 import 改为 `import { Button, Space, Tooltip } from 'antd'`，返回值改为：

```tsx
return (
  <div className="adm-toolbar">
    <div className="adm-toolbar__row">
      <Space wrap size={12}>
        {children}
        {hasFilter && onReset && (
          <Tooltip title={t('common.resetHint')}>
            <Button type="text" size="small" icon={<ReloadOutlined />} onClick={onReset}>
              {t('common.reset')}
            </Button>
          </Tooltip>
        )}
      </Space>
      {extra}
    </div>
  </div>
)
```

- [ ] **Step 3: DataTable 去掉 Card 外壳**

把 import 改为 `import { Table } from 'antd'`，返回值外层改为：

```tsx
return (
  <div className="adm-tablecard">
    <Table<T> ... />
  </div>
)
```

保留其余 Table 配置不变。

- [ ] **Step 4: 重写 adm-common 的页头/工具条/表格样式**

把 `adm-common.css` 中 `.adm-header` 到 `.adm-header__actions`、`.adm-toolbar`、`.adm-tablecard`、`.adm-table` 相关规则替换为：

```css
/* ---- 页头：页面卡卡头（全宽、底部细线） ---- */
.adm-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  min-height: 52px;
  margin: calc(var(--app-page-pad, 16px) * -1) calc(var(--app-page-pad, 16px) * -1) 16px;
  padding: 0 var(--app-page-pad, 16px);
  border-bottom: 1px solid var(--app-border-light);
}

.app-content--edit .adm-header {
  margin-top: 0;
}

.adm-header__main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.adm-header__icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  color: var(--app-primary);
}

.adm-header__title {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--app-text);
}

.adm-header__sub {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 2px;
  font-size: 12px;
  color: var(--app-text-2);
}

.adm-header__sub strong {
  color: var(--app-text-2);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.adm-header__dot {
  color: var(--app-text-4);
}

.adm-sep {
  color: var(--app-text-4);
}

.adm-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* ---- 工具条：无外框 ---- */
.adm-toolbar {
  margin-bottom: 12px;
}

.adm-toolbar__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

/* ---- 表格：直接铺在页面卡上 ---- */
.adm-tablecard {
  min-width: 0;
}

.adm-table .ant-table {
  font-size: 14px;
}

.adm-table .ant-table-thead > tr > th {
  background: var(--app-subtle);
  color: var(--app-text-2);
  font-weight: 600;
  font-size: 13px;
  border-bottom: 1px solid var(--app-border-light);
}

.adm-table .ant-table-tbody > tr > td {
  border-bottom: 1px solid var(--app-border-light);
}

.adm-table .ant-table-tbody > tr:hover > td {
  background: var(--app-table-hover);
}

.adm-table .ant-table-pagination.ant-pagination {
  margin: 16px 0;
}
```

`.adm-selinfo` 改为：

```css
.adm-selinfo {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 4px 2px 10px;
  border-radius: var(--app-radius-pill);
  font-size: 12px;
  color: var(--app-primary);
  background: var(--app-primary-weak);
}
```

- [ ] **Step 5: 运行测试与构建**

Run: `npm test -- ui.test.tsx SettingsPages.smoke.test.tsx`
Expected: PASS。

Run: `npm run build`
Expected: 成功。

- [ ] **Step 6: 提交（仅在获得提交授权时）**

```bash
git add src/components/ListPageHeader.tsx src/components/ui/ListToolbar.tsx src/components/ui/DataTable.tsx src/styles/sections/adm-common.css
git commit -m "feat(admin-ui): 扁平化页头、工具条与表格"
```

---

### Task 7: 状态标签、设置页与表单视觉

**Files:**
- Modify: `src/components/ui/StatusTag.tsx`
- Modify: `src/components/settings/settingsKit.css`

**Interfaces:**
- Produces: `StatusTag` 胶囊样式；设置页导航选中态主色、面板标题橙条、卡片 4px 圆角。

- [ ] **Step 1: StatusTag 改胶囊**

把 `borderRadius: 'var(--app-radius-xs)'` 改为 `borderRadius: 'var(--app-radius-pill)'`。

- [ ] **Step 2: 清扫 settingsKit.css**

按下列映射全文替换（保留 `999px`、`50%`）：

| 旧值 | 新值 |
|---|---|
| `#fff` / `#ffffff`（背景） | `var(--app-card)` |
| `#f0f0f0` | `var(--app-border-light)` |
| `#f5f5f5` | `var(--app-fill)` |
| `#595959` | `var(--app-text-2)` |
| `#262626` | `var(--app-text)` |
| `#8c8c8c` | `var(--app-text-3)` |
| `#389e0d` | `var(--app-success)` |
| `#d46b08` | `var(--app-warning)` |
| `#cf1322` / `#ff4d4f` | `var(--app-danger)` |
| `#faad14` | `var(--app-warning)` |
| `#1677ff` | `var(--app-primary)` |
| `#e6f4ff` | `var(--app-primary-weak)` |
| `border-radius: 10px` / `8px` / `6px` | `border-radius: var(--app-radius)` |
| `border-radius: 9px` | `border-radius: var(--app-radius)` |

另外：

- `.settings-channel`、`.settings-section` 删除阴影（如无则跳过），边框改 `var(--app-border-light)`。
- `.settings-banner` 的 `border-radius` 改 `var(--app-radius)`；左侧色条宽度保持 3px，颜色换 token。
- `.settings-nav` 的右边框改 `var(--app-border-light)`。
- `.settings-panel__head` 加橙条：

```css
.settings-panel__head .ant-typography {
  position: relative;
  padding-left: 12px;
}

.settings-panel__head .ant-typography::before {
  position: absolute;
  top: 50%;
  left: 0;
  width: 4px;
  height: 16px;
  content: '';
  background: var(--app-accent);
  border-radius: 0 var(--app-radius) var(--app-radius) 0;
  transform: translateY(-50%);
}
```

- `.settings-nav__item.is-active` 保持主色文字 + `var(--app-primary-weak)` 底。

- [ ] **Step 3: 运行测试与构建**

Run: `npm test -- ui.test.tsx SettingsPages.smoke.test.tsx`
Expected: PASS（`StatusTag` 仍保留 `color: var(--app-success)`）。

Run: `npm run build`
Expected: 成功。

- [ ] **Step 4: 提交（仅在获得提交授权时）**

```bash
git add src/components/ui/StatusTag.tsx src/components/settings/settingsKit.css
git commit -m "feat(admin-ui): 统一状态标签与设置页视觉"
```

---

### Task 8: 核心 section CSS 清扫（看板/工作台/分析/公共覆盖）

**Files:**
- Modify: `src/styles/sections/dashboard.css`
- Modify: `src/styles/sections/workplace.css`
- Modify: `src/styles/sections/analysis.css`
- Modify: `src/styles/sections/shared-overrides.css`
- Modify: `src/styles/sections/theme-dark.css`
- Modify: `src/styles/sections/responsive.css`

**Interfaces:**
- Produces: 看板/工作台/分析页卡片、页头、图表容器统一 4px 圆角、无阴影、橙条标题；暗色与移动端不回归。

- [ ] **Step 1: 全量定位旧值与硬编码**

Run: `grep -rn -E "#(4263EB|4F6BFF|1677ff|2563eb|5B6CFF|6D5BFF)|rgba\(66, 99, 235" src/styles/sections`
Expected: 得到待替换清单，逐条按 Global Constraints 的颜色映射替换。

- [ ] **Step 2: dashboard.css 样式**

把卡片规则改为：

```css
.dash-card.ant-card {
  border-radius: var(--app-radius);
  border-color: var(--app-border-light);
  box-shadow: none;
}

.dash-card.ant-card:hover {
  box-shadow: none;
}

.dash-metric--clickable.ant-card:hover {
  border-color: var(--app-primary-border);
  transform: none;
}

.dash-metric__title {
  font-size: 13px;
  color: var(--app-text-2);
}

.dash-metric__value {
  font-size: 24px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--app-text);
}
```

把 `.dash-metric__icon--blue/purple/cyan/green/orange/red/geekblue` 的颜色全部改成 token（蓝 `--app-primary` + `--app-primary-weak`，紫 `--app-purple` + `--app-purple-bg`，绿 `--app-success` + `--app-success-bg`，橙 `--app-warning` + `--app-warning-bg`，红 `--app-danger` + `--app-danger-bg`，青/深蓝复用 primary 浅底两档）。删除所有大于 8px 的卡片圆角与卡片阴影。

- [ ] **Step 3: workplace.css 样式**

把 `.wp-hero` 改为：

```css
.wp-hero {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
  overflow: hidden;
  padding: 24px 26px;
  border: 1px solid var(--app-border-light);
  border-radius: var(--app-radius-lg);
  background:
    radial-gradient(700px 220px at 100% -30%, rgba(22, 93, 255, 0.14), transparent 60%),
    linear-gradient(120deg, var(--app-primary-weak), var(--app-card) 58%, var(--app-tint));
}

.wp-hero::after {
  content: '';
  position: absolute;
  right: -40px;
  bottom: -80px;
  width: 220px;
  height: 220px;
  border-radius: 50%;
  border: 28px solid rgba(22, 93, 255, 0.08);
  pointer-events: none;
}
```

把 `.wp-*` 中其余卡片圆角改为 `var(--app-radius)`，阴影删除，主色/灰阶换 token。

- [ ] **Step 4: analysis.css 样式**

把所有 `.ana-*` 卡片的 `border-radius` 改为 `var(--app-radius)`（hero 可用 `--app-radius-lg`），删除卡片阴影，主色与渐变换 `--app-primary` 系 token；KPI 卡 hover 不位移。

- [ ] **Step 5: shared-overrides.css / theme-dark.css**

- `shared-overrides.css` 中标签对比度规则保留，颜色改 token；`.adm-menu-group-active` 主色改 `var(--app-primary)`。
- `theme-dark.css` 中硬编码的橙/蓝装饰改 `var(--app-accent)` / `var(--app-primary)`；卡片阴影删除。

- [ ] **Step 6: 验证无旧品牌色残留**

Run: `grep -rn -E "#(4263EB|4F6BFF|2563eb|5B6CFF|6D5BFF)|rgba\(66, 99, 235" src/styles/sections`
Expected: 无输出。

- [ ] **Step 7: 构建与截图**

Run: `npm run build`
Expected: 成功。

浏览器检查首页、工作台、分析页的亮/暗色：卡片 4px 圆角、无阴影、橙条标题、主色蓝。

- [ ] **Step 8: 提交（仅在获得提交授权时）**

```bash
git add src/styles/sections/dashboard.css src/styles/sections/workplace.css src/styles/sections/analysis.css src/styles/sections/shared-overrides.css src/styles/sections/theme-dark.css src/styles/sections/responsive.css
git commit -m "feat(admin-ui): 对齐看板与看板类页面视觉"
```

---

### Task 9: 其余模块 CSS 与 TSX 内联色清扫

**Files:**
- Modify: `src/styles/sections/*.css`（除已完成的核心文件）
- Modify: `src/components/dashboard/*.tsx`
- Modify: `src/components/analysis/*.ts(x)`
- Modify: `src/pages/Dashboard.tsx`、`src/pages/Analysis.tsx`
- Modify: `src/utils/categoryColor.ts`
- Modify: `src/pages/AiModels/index.css`、`src/pages/LegalDocuments/index.css`、`src/pages/LoginSettings/index.css`
- Modify: `src/components/editor/editorStyles/*.ts`

**Interfaces:**
- Produces: 全站不再出现旧靛蓝/默认 antd 蓝作为品牌色；图表系列色随主题。

- [ ] **Step 1: 定位全部残留**

Run:

```bash
grep -rn -E "#(4263EB|4F6BFF|1677ff|2563eb|5B6CFF|6D5BFF)" src
grep -rn -E "border-radius: (8|10|12|14|16)px" src/styles/sections
grep -rn -E "box-shadow: 0 1px 2px|0 6px 20px|0 10px 26px" src/styles/sections
```

Expected: 列出待处理文件；逐条按映射替换。

- [ ] **Step 2: 替换 TSX/TS 中的品牌色**

- 所有 `#1677ff` / `#2563eb` / `#4263EB` / `#4F6BFF` 改为 `var(--app-primary)`（ECharts SVG、内联 style 均支持 CSS 变量）。
- `Dashboard.tsx` 的 `TREND_METRICS`、`SOURCE_COLORS`、`CONTENT_COLORS` 改为：

```ts
const TREND_METRICS = [
  { value: 'download', label: '下载量', color: 'var(--app-primary)' },
  { value: 'registration', label: '新增注册', color: 'var(--app-success)' },
] as const

const SOURCE_COLORS = { phone: 'var(--app-primary)', email: 'var(--app-purple)' } as const

const CONTENT_COLORS = { tutorials: 'var(--app-success)' } as const
```

- `components/analysis/mockData.ts`、`FunnelBars.tsx`、`SearchBars.tsx`、`components/dashboard/*` 中的系列色按同样规则换 token。
- `utils/categoryColor.ts` 的品牌蓝改 `var(--app-primary)`。
- 编辑器样式中的品牌蓝改 token；语义色保留。

- [ ] **Step 3: 替换模块 CSS 的圆角与阴影**

- 卡片/面板/区块：`border-radius: 8/10/12/14/16px` → `var(--app-radius)`；弹窗/浮层保留 `var(--app-radius-lg)`。
- 胶囊/头像保留 `999px` / `50%`。
- 卡片 `box-shadow: 0 1px 2px ...` 删除；hover 大阴影删除；浮层阴影改 `var(--app-shadow-float)`。
- 重点文件：`adm-edit-page.css`、`codegen.css`、`monitor.css`、`dictionary.css`、`menu-settings.css`、`scheduled-jobs.css`、`audit-logs.css`、`feedback.css`、`announcements.css`、`image-search.css`、`ai-writing.css`、`materials.css`、`todo-bell.css`、`command-palette.css`、`backups.css`、`recycle-bin.css`、`redeem-codes.css`。

- [ ] **Step 4: 验证**

Run: `grep -rn -E "#(4263EB|4F6BFF|1677ff|2563eb|5B6CFF|6D5BFF)" src`
Expected: 无输出（若个别语义色保留，必须在代码注释说明用途）。

Run: `npm test`
Expected: 全部 PASS。

Run: `npm run build`
Expected: 成功。

- [ ] **Step 5: 提交（仅在获得提交授权时）**

```bash
git add src
git commit -m "feat(admin-ui): 清扫全站旧色值与圆角"
```

---

### Task 10: 登录页左右分栏

**Files:**
- Modify: `src/pages/Login.tsx`
- Modify: `src/pages/Login.test.tsx`
- Modify: `src/i18n/messages.ts`
- Create: `src/styles/sections/login.css`
- Modify: `src/styles/custom.css`

**Interfaces:**
- Consumes: `login.*` i18n、`usePrefsStore`、现有登录/TOTP/安装跳转逻辑。
- Produces: `.login-page`、`.login-brand`、`.login-main`、`.login-card` 样式。

- [ ] **Step 1: 增加登录页文案**

中文：

```ts
'login.welcome': '欢迎回来',
'login.heroTitle': '一站式 AI 内容与运营后台',
'login.heroDesc': '基于 React 与 Ant Design，提供权限、内容、运营与系统配置的一站式管理能力。',
'login.feature1Title': '权限体系',
'login.feature1Desc': '菜单与按钮级 RBAC 控制',
'login.feature2Title': '主题定制',
'login.feature2Desc': '亮色 / 暗色 / 跟随系统',
'login.feature3Title': '响应式布局',
'login.feature3Desc': '桌面、平板与移动端适配',
```

英文：

```ts
'login.welcome': 'Welcome back',
'login.heroTitle': 'One-stop AI content & operations console',
'login.heroDesc': 'Built with React and Ant Design: permissions, content, operations and system settings in one place.',
'login.feature1Title': 'Permissions',
'login.feature1Desc': 'Menu- and button-level RBAC',
'login.feature2Title': 'Theming',
'login.feature2Desc': 'Light / dark / follow system',
'login.feature3Title': 'Responsive',
'login.feature3Desc': 'Desktop, tablet and mobile ready',
```

- [ ] **Step 2: 更新 Login 测试**

在 `Login.test.tsx` 追加：

```tsx
it('左侧品牌区与右侧欢迎标题同时存在', () => {
  renderLogin()
  expect(screen.getByText('欢迎回来')).toBeInTheDocument()
  expect(screen.getByText('权限体系')).toBeInTheDocument()
  expect(screen.getByText('响应式布局')).toBeInTheDocument()
})
```

- [ ] **Step 3: 重构 Login 返回结构**

保留全部状态与 `handleSubmit`；把 `return` 替换为：

```tsx
return (
  <div className="login-page">
    <aside className="login-brand" aria-hidden="true">
      <div className="login-brand__logo">
        <img src={`${import.meta.env.BASE_URL}favicon-64.png`} alt="" />
        <span>{t('login.title')}</span>
      </div>
      <div className="login-brand__copy">
        <h1>{t('login.heroTitle')}</h1>
        <p>{t('login.heroDesc')}</p>
      </div>
      <ul className="login-brand__features">
        <li>
          <SafetyOutlined />
          <div>
            <strong>{t('login.feature1Title')}</strong>
            <span>{t('login.feature1Desc')}</span>
          </div>
        </li>
        <li>
          <BulbOutlined />
          <div>
            <strong>{t('login.feature2Title')}</strong>
            <span>{t('login.feature2Desc')}</span>
          </div>
        </li>
        <li>
          <MobileOutlined />
          <div>
            <strong>{t('login.feature3Title')}</strong>
            <span>{t('login.feature3Desc')}</span>
          </div>
        </li>
      </ul>
      <div className="login-brand__footer">© {new Date().getFullYear()} AI学习猫</div>
    </aside>
    <main className="login-main">
      <div className="login-main__lang">
        <Segmented
          size="small"
          value={locale}
          onChange={(value) => setLocale(value as Locale)}
          options={SUPPORTED_LOCALES.map((item) => ({ label: LOCALE_LABELS[item], value: item }))}
        />
      </div>
      <div className="login-card">
        <div className="login-card__head">
          <img className="login-card__logo" src={`${import.meta.env.BASE_URL}favicon-64.png`} alt="" />
          <Typography.Title level={3} style={{ margin: 0 }}>
            {t('login.welcome')}
          </Typography.Title>
          <Typography.Text type="secondary">{t('login.subtitle')}</Typography.Text>
        </div>
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          size="large"
          initialValues={LOGIN_PREFILL}
        >
          <Form.Item
            label={t('login.account')}
            name="account"
            rules={[{ required: true, message: t('login.accountRequired') }]}
          >
            <Input
              prefix={<UserOutlined />}
              placeholder={t('login.accountPlaceholder')}
              autoComplete="username"
            />
          </Form.Item>

          <Form.Item
            label={t('login.password')}
            name="password"
            rules={[{ required: true, message: t('login.passwordRequired') }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder={t('login.passwordPlaceholder')}
              autoComplete="current-password"
            />
          </Form.Item>

          {totpRequired && (
            <Form.Item
              label={t('login.totp')}
              name="totpCode"
              rules={[{ required: true, message: t('login.totpRequired') }]}
              extra={t('login.totpHint')}
            >
              <Input
                prefix={<SafetyOutlined />}
                placeholder={t('login.totpPlaceholder')}
                autoComplete="one-time-code"
                maxLength={12}
              />
            </Form.Item>
          )}

          {error && (
            <Form.Item style={{ marginBottom: 16 }}>
              <Alert title={error} type="error" showIcon />
            </Form.Item>
          )}

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              {loading ? t('login.submitting') : t('login.submit')}
            </Button>
          </Form.Item>
        </Form>
      </div>
      <div className="login-main__footer">© {new Date().getFullYear()} AI学习猫</div>
    </main>
  </div>
)
```

删除旧的外层渐变 div、Card 与右上角 Segmented；import 增加 `BulbOutlined, MobileOutlined`，`Card` 不再使用则删除。

- [ ] **Step 4: 创建登录页样式**

创建 `src/styles/sections/login.css`：

```css
.login-page {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  min-height: 100vh;
  background: var(--app-card);
}

.login-brand {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  overflow: hidden;
  padding: 48px 56px;
  color: #fff;
  background:
    radial-gradient(600px 300px at 90% 0%, rgba(255, 255, 255, 0.18), transparent 60%),
    linear-gradient(135deg, #165dff 0%, #0e42d2 100%);
}

.login-brand__logo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 18px;
  font-weight: 600;
}

.login-brand__logo img {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: 8px;
}

.login-brand__copy h1 {
  margin: 0 0 12px;
  font-size: 34px;
  line-height: 1.3;
  font-weight: 650;
}

.login-brand__copy p {
  max-width: 460px;
  margin: 0;
  font-size: 14px;
  line-height: 1.8;
  color: rgba(255, 255, 255, 0.82);
}

.login-brand__features {
  display: grid;
  gap: 14px;
  padding: 0;
  margin: 32px 0;
  list-style: none;
}

.login-brand__features li {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  font-size: 18px;
}

.login-brand__features div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.login-brand__features strong {
  font-size: 14px;
  font-weight: 600;
}

.login-brand__features span {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.78);
}

.login-brand__footer,
.login-main__footer {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.6);
}

.login-main {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
}

.login-main__lang {
  position: absolute;
  top: 24px;
  right: 24px;
}

.login-card {
  width: 100%;
  max-width: 380px;
}

.login-card__head {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  margin-bottom: 28px;
  text-align: center;
}

.login-card__logo {
  display: none;
  width: 48px;
  height: 48px;
  margin-bottom: 6px;
  object-fit: cover;
  border-radius: 10px;
}

.login-main__footer {
  position: absolute;
  bottom: 24px;
  color: var(--app-text-3);
}

[data-theme='dark'] .login-main {
  background: var(--app-card);
}

@media (max-width: 991px) {
  .login-page {
    grid-template-columns: 1fr;
  }

  .login-brand {
    display: none;
  }

  .login-card__logo {
    display: block;
  }

  .login-main {
    background: var(--app-canvas);
  }
}
```

在 `custom.css` 的 `layout.css` 之后加入：

```css
/* 登录页 */
@import './sections/login.css';
```

- [ ] **Step 5: 运行测试与构建**

Run: `npm test -- Login.test.tsx`
Expected: 原有 3 条 + 新增 1 条 PASS。

Run: `npm run build`
Expected: 成功。

- [ ] **Step 6: 提交（仅在获得提交授权时）**

```bash
git add src/pages/Login.tsx src/pages/Login.test.tsx src/i18n/messages.ts src/styles/sections/login.css src/styles/custom.css
git commit -m "feat(admin-ui): 登录页改为左右分栏品牌布局"
```

---

### Task 11: 首页欢迎卡与看板视觉

**Files:**
- Create: `src/utils/greeting.ts`
- Create: `src/utils/greeting.test.ts`
- Modify: `src/pages/Dashboard.tsx`
- Modify: `src/styles/sections/dashboard.css`

**Interfaces:**
- Produces: `greetingKey(date: Date): 'morning' | 'afternoon' | 'evening'`；首页 `.dash-hero` 欢迎卡。

- [ ] **Step 1: 写问候语测试**

创建 `src/utils/greeting.test.ts`：

```ts
import { describe, expect, it } from 'vitest'
import { greetingKey } from './greeting'

describe('greetingKey', () => {
  it('按小时返回早上/下午/晚上', () => {
    expect(greetingKey(new Date('2026-10-31T08:00:00'))).toBe('morning')
    expect(greetingKey(new Date('2026-10-31T13:00:00'))).toBe('afternoon')
    expect(greetingKey(new Date('2026-10-31T21:00:00'))).toBe('evening')
  })

  it('边界：12 点算下午，18 点算晚上', () => {
    expect(greetingKey(new Date('2026-10-31T12:00:00'))).toBe('afternoon')
    expect(greetingKey(new Date('2026-10-31T18:00:00'))).toBe('evening')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- greeting.test.ts`
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现 greeting.ts**

```ts
export type GreetingKey = 'morning' | 'afternoon' | 'evening'

export function greetingKey(date: Date): GreetingKey {
  const hour = date.getHours()
  if (hour < 12) return 'morning'
  if (hour < 18) return 'afternoon'
  return 'evening'
}
```

- [ ] **Step 4: Dashboard 增加头像与问候数据**

- import `Avatar`、`greetingKey`、`useI18n`（如未使用）。
- 在 `useAuthStore` 处补：

```ts
const account = useAuthStore((s) => s.user?.account)
const avatarUrl = useAuthStore((s) => s.user?.avatar_url)
```

- 在 `data` 之后加：

```ts
const greetingText =
  greetingKey(new Date()) === 'morning'
    ? '早上好'
    : greetingKey(new Date()) === 'afternoon'
      ? '下午好'
      : '晚上好'
const today = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long',
})
```

- [ ] **Step 5: 用欢迎卡替换 `.dash-header`**

把原 `.dash-header` 的 JSX 替换为：

```tsx
<Card className="dash-hero" variant="outlined">
  <div className="dash-hero__main">
    <Avatar size={56} src={avatarUrl || undefined} icon={<UserOutlined />} />
    <div className="dash-hero__copy">
      <div className="dash-hero__title">
        {greetingText}，{account || '管理员'}，开始您一天的工作吧！
      </div>
      <div className="dash-hero__sub">
        {today}
        <span>·</span>
        {updatedAt ? `数据更新于 ${updatedAt}` : '正在加载…'}
      </div>
    </div>
  </div>
  <div className="dash-hero__side">
    <div className="dash-hero__stat">
      <span>总用户</span>
      <strong>{data.total_users}</strong>
    </div>
    <div className="dash-hero__stat">
      <span>待审核</span>
      <strong>{data.pending.auth_requests}</strong>
    </div>
    <div className="dash-hero__stat">
      <span>待处理反馈</span>
      <strong>{data.pending.feedbacks}</strong>
    </div>
    <Button icon={<ReloadOutlined />} onClick={load} loading={loading} disabled={loading}>
      刷新
    </Button>
  </div>
</Card>
```

- [ ] **Step 6: 欢迎卡样式**

在 `dashboard.css` 追加：

```css
.dash-hero.ant-card {
  margin-bottom: 16px;
  border-color: var(--app-border-light);
  border-radius: var(--app-radius);
  box-shadow: none;
}

.dash-hero .ant-card-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
  padding: 20px 24px;
}

.dash-hero__main {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.dash-hero__copy {
  min-width: 0;
}

.dash-hero__title {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--app-text);
}

.dash-hero__sub {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-text-2);
}

.dash-hero__side {
  display: flex;
  align-items: center;
  gap: 28px;
  flex-wrap: wrap;
}

.dash-hero__stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.dash-hero__stat span {
  font-size: 12px;
  color: var(--app-text-2);
}

.dash-hero__stat strong {
  font-size: 22px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--app-text);
}
```

- [ ] **Step 7: 运行测试与构建**

Run: `npm test -- greeting.test.ts dashboardUtils.test.ts`
Expected: PASS。

Run: `npm run build`
Expected: 成功。

- [ ] **Step 8: 提交（仅在获得提交授权时）**

```bash
git add src/utils/greeting.ts src/utils/greeting.test.ts src/pages/Dashboard.tsx src/styles/sections/dashboard.css
git commit -m "feat(admin-ui): 首页新增欢迎卡并统一看板视觉"
```

---

### Task 12: 工作台、分析页收尾与暗色/移动端

**Files:**
- Modify: `src/pages/Workplace/index.tsx`（仅 class 或文案层调整，如需要）
- Modify: `src/pages/Analysis.tsx`
- Modify: `src/styles/sections/workplace.css`
- Modify: `src/styles/sections/analysis.css`
- Modify: `src/styles/sections/theme-dark.css`
- Modify: `src/styles/sections/responsive.css`

**Interfaces:**
- Consumes: Task 8 已建立的 token 视觉。
- Produces: 工作台/分析页亮暗色与移动端验收通过。

- [ ] **Step 1: 工作台收尾**

- `.wp-hero` 的标题、统计数字、eyebrow 全部换 token；
- 工作台内所有卡片圆角 4px、无阴影；
- 移动端 hero 纵向排列，统计区换行。

- [ ] **Step 2: 分析页收尾**

- `.ana-title` 字号 20/600、颜色 token；
- KPI 卡、图表卡圆角 4px、无阴影；
- 图表系列色确认全部为 CSS 变量；
- 移动端 KPI 网格降为单列/双列。

- [ ] **Step 3: 暗色与移动端检查**

- `theme-dark.css` 中删除所有卡片阴影，橙条/主色换 token；
- `responsive.css` 中确保：
  - `.adm-header` 在移动端用 `--app-page-pad: 12px`；
  - 标签页在移动端不渲染（由 AdminLayout 条件控制）；
  - 登录页单栏（见 login.css）。

- [ ] **Step 4: 浏览器验收**

启动 `npm run dev`，在 1440×900 与 390×844 下检查：
- 工作台、分析页亮/暗色；
- 侧栏抽屉、页头换行、表格横向滚动；
- 无横向溢出、无卡片阴影残留。

- [ ] **Step 5: 构建与测试**

Run: `npm test`
Expected: 全部 PASS。

Run: `npm run build`
Expected: 成功。

- [ ] **Step 6: 提交（仅在获得提交授权时）**

```bash
git add src/pages/Workplace/index.tsx src/pages/Analysis.tsx src/styles/sections/workplace.css src/styles/sections/analysis.css src/styles/sections/theme-dark.css src/styles/sections/responsive.css
git commit -m "feat(admin-ui): 收尾工作台与分析页并校准暗色移动端"
```

---

### Task 13: 更新设计规范文档与最终验证

**Files:**
- Modify: `docs/07-开源文档/06-UI设计规范.md`

**Interfaces:**
- Produces: 文档与代码一致的 token/布局规范；最终测试、构建、lint、截图报告。

- [ ] **Step 1: 更新 UI 规范文档**

把 §2.1 品牌色改为 `#165DFF`，§2.2 token 表的画布改 `#F5F7FD`、卡片圆角改 4、控件圆角改 4、小标签圆角改 4、主色改 `#165DFF`；§3.1 骨架图补上标签页条（32px）与页面卡；新增一节「多标签页」描述首页固定、关闭/刷新/持久化/上限 10/移动端隐藏。

- [ ] **Step 2: 全量验证**

Run:

```bash
npm test
npm run build
npm run lint
```

Expected: test 全绿；build 成功；lint 无新增错误（已有 warning 保持原样）。

- [ ] **Step 3: 浏览器截图验收**

使用 Chrome DevTools 打开开发服务，逐页截图检查：

- 亮色 1440×900：登录、首页、用户管理、系统设置、教程编辑、代码生成、监控、个人中心
- 暗色 1440×900：首页、用户管理、系统设置
- 移动端 390×844：登录、首页、用户管理（抽屉菜单）

检查点：主色/橙条/4px 圆角/标签页/表格密度/页面卡；编辑页工具栏吸顶；图表暗色可读；标签页关闭与刷新行为。

- [ ] **Step 4: 提交（仅在获得提交授权时）**

```bash
git add docs/07-开源文档/06-UI设计规范.md
git commit -m "docs: 同步 gi-demo 风格 UI 规范"
```

---

## 验收标准

- [ ] `npm test` 全绿（含 `pageTabs`、`pageTitleStore`、`greeting`、token 守卫）
- [ ] `npm run build` 通过
- [ ] `npm run lint` 无新增错误
- [ ] 亮/暗色下所有核心页面视觉一致：主色 `#165DFF`、橙条、4px 圆角、无卡片阴影
- [ ] 顶栏面包屑、标签页、页面卡结构生效；标签页可关闭/刷新/关闭其他/全部，刷新浏览器保持
- [ ] 移动端抽屉菜单、页头换行、表格滚动正常
- [ ] 编辑页工具栏吸顶、页头不贴边
- [ ] `docs/07-开源文档/06-UI设计规范.md` 与新实现一致
