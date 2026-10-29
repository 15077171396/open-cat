/**
 * 列表页骨架组件（M2）。
 *
 * 从 15 个列表页逐字重复的结构里抽出来的一层，不改任何业务逻辑：
 *
 * ```tsx
 * <PageHeader icon title subtitle count actions />
 * <ListToolbar hasFilter onReset extra>{filters}</ListToolbar>
 * <DataTable columns dataSource pagination empty />
 * ```
 *
 * 三条使用原则：
 * 1. **允许逃生**：复杂页面（章节树、素材库、定时任务）继续手写，不必强行套壳；
 * 2. **组件只管骨架**：数据编排仍由页面 + `useCrudList` 负责，组件不内置请求；
 * 3. **样式走 token**：颜色/圆角来自 `tokens.css` 的 `--app-*`，不要在页面里写死色值。
 */

export { default as PageHeader } from './PageHeader'
export type { PageHeaderProps } from './PageHeader'

export { default as ListToolbar } from './ListToolbar'
export type { ListToolbarProps } from './ListToolbar'

export { default as DataTable } from './DataTable'
export type { DataTableProps, DataTableEmptyConfig } from './DataTable'

export { default as StatusTag, boolTone } from './StatusTag'
export type { StatusTagProps, StatusTone } from './StatusTag'

export { default as SelectionBar } from './SelectionBar'
export type { SelectionBarProps } from './SelectionBar'

export { default as ModalForm } from './ModalForm'
export type { ModalFormProps } from './ModalForm'
