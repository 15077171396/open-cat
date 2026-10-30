import type { ReactNode } from 'react'
import { Button, Empty } from 'antd'
import { PermGuard } from './PermGuard'
import { useI18n } from '../i18n'

/**
 * 后台列表页的统一空态（第五轮审查 MNT-09）。
 *
 * 15 个 `pages/<模块>/List.tsx` 里这段 JSX 逐字重复了 15~20 行，而且判定已经漂移：
 * 有的漏了 `hasFilter` 判断（筛选无结果时也提示「还没有数据」），有的在筛选态下
 * 仍然显示「新建」按钮。
 *
 * ```tsx
 * <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasFilter ? '没有符合…' : '还没有…'}>
 *   {hasFilter ? <Button onClick={reset}>清空筛选条件</Button>
 *              : <PermGuard perm="x:create"><Button type="primary" …>新建 X</Button></PermGuard>}
 * </Empty>
 * ```
 *
 * 现在三种分支只在这里判定一次：**筛选态只给「清空筛选」，空库态只给「新建」**。
 */

export interface TableEmptyProps {
  /** 当前是否处于「筛选后无结果」状态（决定文案与是否给「清空筛选」） */
  hasFilter: boolean
  /** 筛选无结果时的文案 */
  filteredText: string
  /** 一条数据都没有时的文案 */
  emptyText: string
  /** 清空筛选的回调（有筛选时才渲染按钮） */
  onReset?: () => void
  /** 新建入口：没有数据时展示。`perm` 不传则不包权限守卫 */
  create?: {
    perm?: string
    icon?: ReactNode
    label: string
    onClick: () => void
  }
}

export function TableEmpty({ hasFilter, filteredText, emptyText, onReset, create }: TableEmptyProps) {
  const { t } = useI18n()
  const createButton = create ? (
    <Button type="primary" icon={create.icon} onClick={create.onClick}>
      {create.label}
    </Button>
  ) : null

  return (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasFilter ? filteredText : emptyText}>
      {hasFilter ? (
        onReset ? <Button onClick={onReset}>{t('common.clearFilters')}</Button> : null
      ) : create?.perm ? (
        <PermGuard perm={create.perm}>{createButton}</PermGuard>
      ) : (
        createButton
      )}
    </Empty>
  )
}
