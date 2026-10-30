import type { ReactNode } from 'react'
import type { TableProps } from 'antd'
import { Table } from 'antd'
import { TableEmpty } from '../TableEmpty'
import { useI18n } from '../../i18n'

export interface DataTableEmptyConfig {
  /** 当前是否「筛选后无结果」（决定文案与是否给「清空筛选」） */
  hasFilter: boolean
  /** 筛选无结果时的文案 */
  filteredText: string
  /** 一条记录都没有时的文案 */
  emptyText: string
  /** 清空筛选 */
  onReset?: () => void
  /** 空库时的新建入口（带权限守卫） */
  create?: {
    perm?: string
    icon?: ReactNode
    label: string
    onClick: () => void
  }
}

/**
 * 在 antd `TableProps` 之上只覆盖「分页 / 空态 / 滚动宽度」三项：
 * 其余属性（rowSelection / expandable / rowClassName / onRow / size …）原样透传，
 * 页面不必为了用一个 Table 特性就去绕开本组件 —— 那正是「封装一半」最常见的退化方式。
 */
export interface DataTableProps<T>
  extends Omit<
    TableProps<T>,
    'columns' | 'dataSource' | 'rowKey' | 'loading' | 'scroll' | 'pagination' | 'locale' | 'className'
  > {
  columns: TableProps<T>['columns']
  dataSource: T[]
  rowKey?: string | ((record: T) => string)
  loading?: boolean
  /** 横向滚动宽度：列宽之和（含固定操作列），不给则窄屏会挤压 */
  scrollX?: number
  /**
   * 分页：直接接收 `useCrudList` 的返回值。
   * 不传 = 不渲染分页（客户端一次性渲染的树/小表，例如角色、分类、部门）。
   */
  pagination?: {
    page: number
    pageSize: number
    total: number
    onPageChange: (page: number) => void
  }
  /** 空态配置：必传，避免出现「没有任何提示的空表」 */
  empty: DataTableEmptyConfig
  /** 额外类名（模块专属样式挂这里，不要改组件内部） */
  className?: string
}

/**
 * 列表页表格容器。
 *
 * 把「卡片外壳 + 表格类名 + 滚动宽度 + 空态 + 分页口径」收成一处。
 * 此前每个列表页都要重复：
 *
 * ```tsx
 * <Card className="adm-tablecard" variant="outlined">
 *   <Table className="adm-table" rowKey="id" loading={loading} scroll={{ x }}
 *     locale={{ emptyText: <TableEmpty … /> }}
 *     pagination={{ current: page, pageSize, total, onChange: setPage,
 *                   showTotal: t => `共 ${t} 条`, showSizeChanger: false }} />
 * </Card>
 * ```
 *
 * 三条硬约定（改之前先读）：
 * 1. **分页不换页大小**：`showSizeChanger: false`。后端各列表接口不保证支持任意 pageSize，
 *    开放切换会出现「选了 100 条但只回 20 条」的错位；
 * 2. **空态不显示分页**：0 条数据还挂一行「共 0 条」是纯噪音；
 * 3. **`scrollX` 必须覆盖列宽之和**，否则固定操作列（`fixed: 'right'`）会在中屏压出横向重叠。
 */
export default function DataTable<T extends object>({
  columns,
  dataSource,
  rowKey = 'id',
  loading,
  scrollX,
  pagination,
  empty,
  className,
  ...rest
}: DataTableProps<T>) {
  const { t } = useI18n()
  const emptyNode = (
    <TableEmpty
      hasFilter={empty.hasFilter}
      filteredText={empty.filteredText}
      emptyText={empty.emptyText}
      onReset={empty.onReset}
      create={empty.create}
    />
  )

  const isEmpty = !loading && dataSource.length === 0

  return (
    <div className="adm-tablecard">
      <Table<T>
        {...rest}
        className={className ? `adm-table ${className}` : 'adm-table'}
        dataSource={dataSource}
        columns={columns}
        rowKey={rowKey}
        loading={loading}
        scroll={scrollX ? { x: scrollX } : undefined}
        locale={{ emptyText: emptyNode }}
        pagination={
          isEmpty || !pagination
            ? false
            : {
                current: pagination.page,
                pageSize: pagination.pageSize,
                total: pagination.total,
                onChange: pagination.onPageChange,
                showTotal: (total) => t('common.total', { total }),
                showSizeChanger: false,
              }
        }
      />
    </div>
  )
}
