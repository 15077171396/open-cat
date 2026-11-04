# 管理后台 UI 改版设计（对标 Gi Admin Pro）

- 日期：2026-10-31
- 状态：已实施（2026-10-31，前端 438 个测试 / build / lint 全绿）
- 范围：`admin-frontend`（开源后台）全部页面与样式
- 参考：https://gitee.com/lin0716/gi-demo （在线预览 https://lin-97.github.io/gi-demo/）

## 1. 目标与非目标

### 目标

1. 把现有「靛蓝 + 大圆角 + 大卡片」的视觉，改为 gi-demo 的「蓝 + 橙点缀 + 4px 小圆角 + 浅灰画布」。
2. 补齐 gi-demo 的布局结构：顶栏面包屑、**多标签页**、白色页面卡、扁平工具条与表格。
3. 用一套 token 同时驱动 antd 组件与手写 CSS，亮/暗色、侧边/顶部布局、移动端、i18n 全部保留。
4. 统一散落在 40+ 页面里的页头、卡片、表格、表单、按钮、标签、分页样式。

### 非目标

- 不引入新框架、UI 库或 Web 字体；仍是 React 18 + Ant Design 6 + Vite。
- 不改后端接口、RBAC、路由表、菜单下发逻辑与业务功能。
- 不重写页面业务代码；只改样式、共享组件与布局外壳。
- 不照搬 gi-demo 的 Vue/Arco 实现细节，只对齐视觉与交互结果。

## 2. 参考项目实测结论

从 gi-demo 在线预览抓取的计算样式：

| 项 | 实测值 |
|---|---|
| 主色 | `#165DFF`（hover `#4080FF`，active `#0E42D2`） |
| 点缀橙 | `#FF9A2E`（卡片标题左侧 4px 竖条，右端 4px 圆角） |
| 画布 | `#F5F7FD` |
| 卡片 | `#FFFFFF`，圆角 4px，无阴影 |
| 文字 | `#121314` / `#5C5C5C` / `#909399` / `#C2C2C2` |
| 边框 | `#E5E6EB` / `#EBEEF5` |
| 侧栏 | 220px 白底，`0 2px 5px rgba(0,0,0,.08)`，logo 区 56px |
| 菜单项 | 40px 高，4px 圆角；选中态 `#F2F3F5` 底 + 蓝字 |
| 顶栏 | 56px 白底，底边线 `#E5E6EB`，面包屑 + 右侧工具 |
| 标签页 | 31px 白底，文字型标签，当前项蓝字，可关闭 |
| 页面卡 | 浅灰画布上白色卡片，外层 margin 14px，卡头 44px，正文 16px |
| 表格 | 表头 `#FAFAFA` / 38px，行 41px，无竖线 |
| 按钮 | 高 29px，4px 圆角，实心蓝主按钮、实心红危险按钮 |
| 状态标签 | 22px 高，胶囊形，浅底 + 同色字 |

## 3. 设计 Token

`src/theme.ts` 与 `src/styles/sections/tokens.css` 两处同步维护，取值必须一致。

### 3.1 颜色 · 亮色

| Token | 值 | 用途 |
|---|---|---|
| `--app-primary` | `#165DFF` | 主色、链接、选中菜单、图表主色 |
| `--app-primary-hover` | `#4080FF` | hover |
| `--app-primary-active` | `#0E42D2` | pressed |
| `--app-primary-weak` | `#E8F3FF` | 浅底、信息条、选中背景辅助 |
| `--app-primary-border` | `#BEDAFF` | 主色描边 |
| `--app-accent` | `#FF9A2E` | 卡片标题竖条、装饰性强调 |
| `--app-accent-bg` | `#FFF5E8` | 橙色浅底 |
| `--app-canvas` | `#F5F7FD` | 页面画布 |
| `--app-card` | `#FFFFFF` | 卡片 / 页面卡 / 顶栏 / 侧栏 |
| `--app-subtle` | `#FAFAFA` | 表头、次级填充 |
| `--app-fill` | `#F2F3F5` | 菜单选中、hover 填充、中性标签 |
| `--app-fill-strong` | `#E5E6EB` | 更深的填充 / 禁用底 |
| `--app-border` | `#E5E6EB` | 主边框 |
| `--app-border-light` | `#EBEEF5` | 卡片、表格分隔线 |
| `--app-border-strong` | `#DCDFE6` | 输入框、强描边 |
| `--app-text` | `#121314` | 主文字、标题 |
| `--app-text-2` | `#5C5C5C` | 正文、次要文字（白底 6.5:1） |
| `--app-text-3` | `#909399` | 辅助说明、占位（仅限非关键文字） |
| `--app-text-4` | `#C2C2C2` | 分隔点、装饰 |
| `--app-success` | `#16A34A` | 成功文字/图标（沿用现有 AA 取值） |
| `--app-success-bg` | `#EAF7EE` | 成功浅底 |
| `--app-warning` | `#D97706` | 警告文字（沿用现有 AA 取值） |
| `--app-warning-bg` | `#FFF7E8` | 警告浅底 |
| `--app-danger` | `#CF1322` | 危险文字 / 按钮（白字 5.5:1） |
| `--app-danger-bg` | `#FDECEC` | 危险浅底 |
| `--app-info-bg` | `#E8F3FF` | 信息条浅底 |
| `--app-purple` | `#722ED1` | 次要图表 / 标签 |
| `--app-purple-bg` | `#F4F0FF` | 紫色浅底 |
| `--app-elevated` | `#FFFFFF` | 下拉、弹窗、浮层 |
| `--app-mask` | `rgba(18, 19, 20, .45)` | 遮罩 |

说明：

- gi-demo 的 `#F53F3F` 白字对比度仅 3.7:1，**不用于正文/按钮文字**；危险操作统一用 `#CF1322`。
- `#909399` 仅用于辅助标签；表头文字改用 `#5C5C5C`，避免 14px 小字低于 AA。
- 橙色只做装饰（标题竖条、强调点），不承担状态语义。

### 3.2 颜色 · 暗色

| Token | 值 |
|---|---|
| `--app-primary` | `#4080FF` |
| `--app-primary-hover` | `#6AA1FF` |
| `--app-primary-active` | `#165DFF` |
| `--app-primary-weak` | `#1E2A54` |
| `--app-primary-border` | `#2D3A6B` |
| `--app-accent` | `#FFB65C` |
| `--app-accent-bg` | `#2B2313` |
| `--app-canvas` | `#0F1115` |
| `--app-card` | `#161A21` |
| `--app-elevated` | `#1B2029` |
| `--app-subtle` | `#1B2029` |
| `--app-fill` | `#21262F` |
| `--app-fill-strong` | `#2A303A` |
| `--app-border` | `#262B35` |
| `--app-border-light` | `#20252E` |
| `--app-border-strong` | `#3A4250` |
| `--app-text` | `#E6E9EF` |
| `--app-text-2` | `#A6ADBB` |
| `--app-text-3` | `#8B93A1` |
| `--app-text-4` | `#5F6672` |
| `--app-success` | `#3FB950` |
| `--app-warning` | `#D29922` |
| `--app-danger` | `#F85149` |
| `--app-info-bg` | `#14243B` |
| `--app-purple` | `#B37FEB` |
| `--app-mask` | `rgba(0, 0, 0, .55)` |

暗色画布/卡片/边框沿用现有暗色体系，只替换主色与点缀橙。

### 3.3 形状、字号与间距

| Token | 亮/暗共用值 |
|---|---|
| `--app-radius` | 4px（卡片、按钮、输入框、标签、分页） |
| `--app-radius-sm` | 4px |
| `--app-radius-xs` | 4px |
| `--app-radius-lg` | 8px（弹窗、抽屉圆角、浮层） |
| `--app-radius-pill` | 999px（状态标签、头像） |
| `--app-shadow` | `none`（卡片） |
| `--app-shadow-float` | `0 6px 16px rgba(18, 19, 20, .08)` |
| `--app-gap` | 16px |
| `--app-page-pad` | 16px |
| `--app-header-h` | 56px |
| `--app-tabs-h` | 32px |

字号：

| 角色 | 字号 / 字重 |
|---|---|
| 页面标题 | 16 / 600 |
| 卡片标题 | 16 / 500 |
| 区块小标题 | 14 / 600 |
| 正文 | 14 / 400 |
| 表格正文 | 14 / 400 |
| 表头 | 13 / 600 |
| 辅助文字 | 12 / 400 |
| 大数字 | 24-28 / 600，`font-variant-numeric: tabular-nums` |

字体栈沿用系统字体，仅在最前面加入可选的 `Inter`（不加载 Web 字体）：

```
Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
'Hiragino Sans GB', 'Microsoft YaHei', 'Source Han Sans SC',
'Noto Sans SC', 'Noto Sans CJK SC', sans-serif
```

## 4. 布局外壳

### 4.1 骨架

```
┌ 侧栏 220 ┬ 顶栏 56：折叠 · 面包屑 · 搜索/待办/语言/主题/布局/头像 ┐
│ logo 56  │ 标签页 32：首页 × | 用户管理 × | ...      刷新 · 关闭 ▾  │
│ 菜单     ├──────────────────────────────────────────────────────┤
│          │ 画布 #F5F7FD，padding 16                              │
│          │ ┌ 白色页面卡 radius 4 ─────────────────────────────┐  │
│          │ │ 页头：标题 + 副标题 ·············  操作按钮      │  │
│          │ ├──────────────────────────────────────────────────┤  │
│          │ │ 工具条 / 表格 / 表单（16px 内边距）              │  │
│          │ └──────────────────────────────────────────────────┘  │
└──────────┴──────────────────────────────────────────────────────┘
```

- 侧边/顶部两种导航布局都保留；顶部布局时标签页仍在顶栏下方。
- 侧栏：白底，宽 220 / 折叠 80，右缘 `box-shadow: 0 2px 5px rgba(0,0,0,.08)`。
- logo 区 56px：`logo.png` 28×28、圆角 6px + 品牌文字 16/600；折叠只显示 logo。
- 菜单：项高 40、4px 圆角、左右 8px 外边距；选中 = `--app-fill` 底 + 主色文字/图标；分组标题 12px `--app-text-3`；保留手风琴展开与折叠态分组主色标记。
- 顶栏：56px 白底、底边线；左侧折叠按钮 + 面包屑（分组 / 页面）；右侧 32×32 图标按钮，hover `--app-fill`，4px 圆角；保留搜索命令面板、待办、语言、主题、布局、头像菜单。
- 内容区：滚动只发生在这里；画布背景 `--app-canvas`。

### 4.2 页面卡与看板页

- **标准页**（列表、表单、设置、编辑、详情）：内容区是一张白色页面卡，圆角 4、无阴影、`16px` 内边距；页头作为卡头，全宽（用负外边距抵消内边距）、底部细线，卡头高 52px。
- **看板页**（`/`、`/workplace`、`/analysis`）：内容区透明直铺画布，卡片各自为白卡；首页新增欢迎卡。
- **编辑页**（`/tutorials/new`、`/tutorials/:id/edit`）：页面卡上下内边距为 0，保持编辑器工具栏吸顶行为，页头单独补上边距。

### 4.3 多标签页

组件：`src/components/Layout/PageTabs.tsx`；纯函数：`src/utils/pageTabs.ts`；标题注册：`src/store/pageTitleStore.ts`。

行为：

1. 首页 `/` 固定为第一个标签，不可关闭。
2. 路由变化时把当前 `pathname` 加入标签列表，并激活；同一路径不重复。
3. 标题解析优先级：
   - `PageHeader` 通过 `useRegisterPageTitle(title)` 注册的标题（覆盖编辑页、新建页等非菜单路由）；
   - 菜单树中该路径的菜单名（后端已按语言下发）；
   - 兜底 `t('tabs.untitled')`。
4. 关闭：
   - 关闭当前激活标签 → 跳到左侧相邻标签；没有则跳首页；
   - 关闭非激活标签 → 路由不变；
   - 「关闭其他」「关闭全部」始终保留首页。
5. 刷新：重新挂载当前页面（`RouteErrorBoundary` 的 `key` 变化），不整页 reload。
6. 上限 10 个；超出时淘汰最旧的、非首页且非当前的标签。
7. 持久化：只存路径 key 到 `sessionStorage`，标题每次按当前语言重新解析；权限变化后菜单中已不存在的标签自动丢弃。
8. 移动端（<768px）隐藏标签条，保留抽屉菜单。
9. 语言切换后标签标题跟随菜单/页面重新解析，不出现旧语言残留。

## 5. 页面与组件规范

### 5.1 页头（PageHeader / ListPageHeader）

- 52px 高，全宽卡头，底部 `1px solid --app-border-light`。
- 左侧：16px 主色图标（不再使用 44px 图标块）+ 标题 16/600；副标题 12px `--app-text-2`，支持 `·` 分隔与「共 N 条」。
- 右侧：操作区，次要操作在前、主操作在后；按钮间距 8px。
- `ListPageHeader` 改为对 `PageHeader` 的轻量封装，统一视觉；`SettingsEditor` 一并切换。
- 页头不整体包权限守卫；权限只包按钮。

### 5.2 卡片

- 白底、4px 圆角、`1px solid --app-border-light`、无阴影。
- 有标题的卡片：卡头 44px、左右内边距 16px；标题 16/500；标题前 4px 橙色竖条（高 50%、右端 4px 圆角）；卡头不额外加底边线（页面卡除外）。
- 卡体 16px 内边距；卡与卡间距 16px。
- 可点击指标卡 hover：边框变主色浅边，不位移、不放大阴影（保持扁平）。

### 5.3 工具条与表格

- 工具条：**去掉外框卡片**，改成一行扁平区域：左侧筛选（32px 控件）、右侧搜索/刷新/提示；底部与表格间距 12px。
- 表格：表头 `--app-subtle`、13/600、文字 `--app-text-2`；行高 44；仅横线 `--app-border-light`；hover `#F7F9FC`（暗色 `--app-fill`）；无竖线。
- 表格内操作列：保留文字按钮（蓝/红），尺寸 `small`，间距 8px；不做实心小按钮，避免每行过重。
- 分页：右对齐，28px 方形项、4px 圆角；当前页实心主色 + 白字；总数文字 12px 辅助色。
- 选择态：表头选择后显示 `SelectionBar`，浅主色底、4px 圆角。

### 5.4 表单、弹窗、抽屉

- 表单标签 13/500 `--app-text-2`；输入框 32px 高、4px 圆角、边框 `--app-border-strong`；聚焦主色描边。
- 表单分组使用白卡 + 橙色竖条标题；分组间距 16px。
- 弹窗：8px 圆角、标题 16/600、内容 14px、底部按钮右对齐；遮罩 `--app-mask`。
- 抽屉：头部 56px、标题 16/600；内容内边距 16px。
- 下拉/选择器弹层：8px 圆角、`--app-shadow-float`、选项 32px 高、hover `--app-fill`。

### 5.5 按钮、标签、提示

- 按钮：高度 32（小 24 / 大 40）、4px 圆角、无阴影；主按钮实心 `--app-primary`；次按钮白底 `--app-border-strong` 描边；危险按钮红字，批量删除可用实心 `--app-danger`。
- 状态标签（`StatusTag`）：胶囊形、22px 高、12/500、浅底 + 同色字；语义映射保持现有 `StatusTag` 逻辑。
- 普通标签（分类、计数）：4px 圆角、20px 高、`--app-fill` 底 + `--app-text-2` 字。
- 提示条（Alert）：4px 圆角；信息 `--app-info-bg` + 主色图标；成功/警告/危险使用对应浅底。
- 空态：沿用 antd `Empty`，描述 13px `--app-text-3`；表格空态在表体内居中。
- 加载：骨架屏底色 `--app-fill`；页面级 spinner 改用主色。

### 5.6 滚动条

宽 6-8px、圆角 4px、thumb `--app-border-strong`，hover `--app-text-3`，轨道透明。

## 6. 特色页面

### 6.1 登录页

- 桌面 ≥992px：左右分栏。
  - 左侧 50%：主色渐变（`#165DFF → #0E42D2`）+ 圆形装饰；猫 logo 56px 白底圆角、产品名「AI学习猫 管理后台」、一句说明、3 条卖点（权限、主题、组件）。
  - 右侧 50%：白底，表单宽 380px 居中；标题 24/600「欢迎回来」+ 说明；输入 40px；主按钮 40px 全宽；语言切换右上角；底部版权。
- 移动端：隐藏左栏，顶部显示 logo + 标题，表单卡片铺满。
- 暗色：右栏 `--app-card`，左栏渐变加深；表单、按钮、错误提示跟随 token。
- 保留：TOTP 二次验证、开发环境预填、`/setup/status` 跳转、错误提示、i18n。

### 6.2 首页看板

- 内容区改为画布直铺。
- 顶部新增欢迎卡：头像（账号头像）、问候语（早上好/下午好/晚上好）、日期、右侧核心数字（总用户 / 待审核认证 / 待处理反馈）与刷新按钮；白卡、4px 圆角。
- 原有指标卡、趋势图、环形图、AI 用量、系统状态、漏斗、归因面板结构保留；统一卡头橙条、小圆角、扁平边框。
- 图表配色从 `#1677ff/#52c41a` 等改为 `var(--app-primary)` 与语义色；ECharts 的 `extraCssText` 圆角改 8px。

### 6.3 工作台与分析

- 保留现有 `wp-hero` / `ana-hero` 结构与数据逻辑。
- hero 圆角改 8px，渐变改为 `#165DFF` 系浅底；装饰圆环、eyebrow 保留但降低饱和度。
- 卡片统一 4px 圆角、橙条标题、扁平边框；分析页 KPI 卡的主色跟随 token。

### 6.4 安装向导 / 个人中心等

- 安装向导：居中单卡，使用新 token 与 8px 圆角；保留全部步骤逻辑。
- 个人中心、监控、代码生成等页面：通过 token 与共享组件自动跟随；页面专有 CSS 中的硬编码色值按第 7 节清扫。

## 7. 样式实现架构

### 7.1 文件与顺序

| 文件 | 职责 |
|---|---|
| `src/theme.ts` | antd `ThemeConfig`：颜色、圆角、控件高度、组件 token（Menu/Table/Card/Button/Modal/Tabs/Pagination/Input/Tag/Drawer/Alert） |
| `src/styles/sections/tokens.css` | CSS 变量，与 theme.ts 一一对应，含暗色覆盖 |
| 新增 `src/styles/sections/antd-overrides.css` | antd 组件级细节覆盖（卡头橙条、菜单/标签页/表格/分页/弹窗/抽屉/提示的视觉），用双类名提高特异性；在 `custom.css` 中排在 `theme-dark.css` 之后、`responsive.css` 之前 |
| `src/styles/sections/base.css` | 字体栈、滚动条、spinner、错误页兜底 |
| `src/styles/sections/adm-common.css` | 页头、工具条、表格、选中条等通用片段 |
| 其余 section CSS | 逐文件清扫硬编码色值与圆角，不改选择器结构 |

### 7.2 硬编码清扫规则

| 现有值 | 替换为 |
|---|---|
| `#4263EB` / `#4F6BFF` / `#1677ff` / `#2563eb` / `#5B6CFF` / `#6D5BFF` | `var(--app-primary)`（hover/active 用对应变量） |
| `#2563eb` 作为渐变/装饰 | `var(--app-primary)` 或 `--app-primary-weak` |
| `#8c8c8c` / `#999` / `#8b93a1` | `var(--app-text-3)` |
| `#595959` / `#606266` | `var(--app-text-2)` |
| `#262626` / `#1f2329` | `var(--app-text)` |
| `#f0f0f0` / `#f5f5f5` / `#fafafa` | `var(--app-border-light)` / `var(--app-subtle)` |
| `#e6f4ff` | `var(--app-info-bg)` |
| `#52c41a` | `var(--app-success)` |
| `#faad14` / `#fa8c16` / `#d97706` | `var(--app-warning)`（装饰用 `--app-accent`） |
| `#ff4d4f` / `#cf1322` / `#a8071a` | `var(--app-danger)` |
| `border-radius: 8/10/12/14/16px`（卡片、控件、标签） | `var(--app-radius)`；弹窗/浮层用 `var(--app-radius-lg)` |
| `border-radius: 999px` / `50%` | 保留 |
| 卡片 `box-shadow: 0 1px 2px ...` / hover 大阴影 | 删除；浮层才保留阴影 |

TSX 内联色值：

- 品牌蓝统一改 `BRAND.primary` 或 `var(--app-primary)`；
- 图表系列色改 CSS 变量字符串（ECharts SVG 渲染器支持）；
- 语义色（成功/警告/危险）改 `theme` 常量或 CSS 变量；
- 头像/品牌渐变保留，但改为新主色。

### 7.3 组件改动

| 组件 | 改动 |
|---|---|
| `components/Layout/AdminLayout.tsx` | 面包屑、页面卡/画布模式、侧栏 logo、头部操作区、内容滚动与编辑页适配；接入 `PageTabs`、标题注册 Provider、刷新 key |
| 新增 `components/Layout/PageTabs.tsx` | 标签页渲染、关闭/刷新/关闭其他/全部、横向滚动、移动端隐藏 |
| 新增 `utils/pageTabs.ts` | 纯函数：`addTab`、`removeTab`、`closeOthers`、`closeAll`、`limitTabs`、`readTabs/writeTabs`；单测覆盖 |
| 新增 `store/pageTitleStore.ts` | `pathname -> title` 注册表；`PageHeader` 写入，`PageTabs` 读取 |
| `components/ui/PageHeader.tsx` | 新视觉；注册标题；保留 icon/subtitle/count/actions API |
| `components/ListPageHeader.tsx` | 改为 `PageHeader` 封装，避免两套页头 |
| `components/ui/ListToolbar.tsx` | 去掉 Card 外框，改扁平工具条 |
| `components/ui/DataTable.tsx` | 表头/行高/边框/hover/分页统一样式 |
| `components/ui/ModalForm.tsx` | 弹窗圆角、标题、底部按钮 |
| `components/ui/StatusTag.tsx` | 胶囊标签 + 新语义浅底 |
| `components/ui/SelectionBar.tsx` | 新选中态配色 |
| `components/ui/ExportButton.tsx` | 与次按钮统一 |
| `components/settings/*` | 设置导航/面板、字段行、提示条切到新 token；`SettingsEditor` 用新页头 |
| `pages/Login.tsx` | 左右分栏；新增登录页 CSS |
| `pages/Dashboard.tsx` + `components/dashboard/*` | 欢迎卡、卡头橙条、图表色、扁平卡 |
| `pages/Workplace/index.tsx`、`pages/Analysis.tsx` | hero 与卡片视觉调整 |
| `i18n/messages.ts` | 新增 `tabs.*`、`login.hero*` 等键，中英同步 |

## 8. 响应式与可访问性

### 8.1 响应式

- `<768px`：隐藏标签条；侧栏改抽屉；内容区边距 12px；页头纵向堆叠，操作区换行；页面卡圆角保留 4px。
- 表格：沿用现有横向滚动/卡片化断点（1025px）与三页移动端卡片化逻辑，只改视觉 token。
- 登录页：`<992px` 隐藏左侧品牌栏。
- 看图页面（分析/看板）图表容器保持现有自适应逻辑，不改数据逻辑。

### 8.2 可访问性

- 正文 `--app-text-2`（白底 6.5:1）、主色实心按钮白字 5.15:1，均达 WCAG AA。
- 表头文字从 `#909399` 调整为 `--app-text-2`，避免小字低对比。
- 保留所有 `aria-label`、键盘可达性、focus-visible 样式；focus 环用主色 2px。
- 标签页关闭按钮提供 `aria-label`；刷新/关闭菜单键盘可达。
- 暗色下图表轴线、文字继续使用 CSS 变量，随主题切换。

## 9. 实施顺序

1. **基础层**：`theme.ts` + `tokens.css` + `antd-overrides.css` 骨架；全站颜色/圆角先切过去。
2. **外壳层**：`AdminLayout` 面包屑、侧栏 logo、页面卡/画布模式；新增 `PageTabs` + `pageTabs` 纯函数 + `pageTitleStore` + 测试；`PageHeader` 注册标题。
3. **共享组件层**：PageHeader / ListPageHeader / ListToolbar / DataTable / ModalForm / StatusTag / SelectionBar / ExportButton / settings 组件。
4. **页面清扫层**：24 个 section CSS + TSX 内联色；列表页 → 设置页 → 编辑页 → 监控/代码生成等逐批检查。
5. **特色页**：登录页、首页看板、工作台、分析页。
6. **收尾**：暗色/移动端逐页检查；更新 `docs/07-开源文档/06-UI设计规范.md`；跑测试、构建、lint；截图留档。

每批完成后运行 `npm test` 与 `npm run build`，并用浏览器截图检查，避免跨页回归。

## 10. 验证清单

- [ ] `npm test` 全绿（含新增 `pageTabs` 测试）
- [ ] `npm run build` 通过（tsc + vite）
- [ ] `npm run lint` 无新增错误
- [ ] 亮色：登录、首页、用户列表、系统设置、教程编辑、代码生成、监控、个人中心
- [ ] 暗色：上述页面抽查，图表/表格/弹窗/抽屉正常
- [ ] 移动端 390×844：抽屉菜单、页头换行、表格滚动、登录页单栏
- [ ] 标签页：打开多个、关闭当前/其他/全部、刷新、语言切换、刷新浏览器保持、权限删除后清理
- [ ] 侧边/顶部布局切换正常，面包屑与标签同步
- [ ] 编辑页工具栏吸顶、无镂空；页头不贴边
- [ ] 对比 gi-demo：主色、橙条、4px 圆角、标签页、表格密度、页面卡结构一致

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| 69 个页面硬编码色值/圆角遗漏 | 按第 7.2 节规则全量 grep + 逐 section diff 审查；截图抽查 |
| antd 6 CSS-in-JS 注入顺序不可控 | 组件覆盖用双类名；必要时局部 `!important`（仅限视觉细节）；不改业务选择器 |
| 标签页与编辑页/动态路由冲突 | 标签 key 用完整 pathname；标题由 PageHeader 注册；编辑页刷新 key 重新挂载 |
| 暗色下橙色/主色对比不足 | 暗色单独定义 accent/primary；图表用 CSS 变量；暗色截图检查 |
| 移动端已有响应式规则被覆盖 | `responsive.css` 保持最后加载；新覆盖放在它之前 |
| 大范围视觉改动引起测试断言失败 | 测试只断言结构/文案，不断言颜色；新增纯函数测试；逐批跑测试 |

## 12. 交付物

- 更新后的 `admin-frontend` 源码与样式
- 新增 `PageTabs` 与测试
- 更新后的 `docs/07-开源文档/06-UI设计规范.md`
- 浏览器截图（亮/暗、桌面/移动）用于验收
