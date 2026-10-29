import type { ReactNode } from 'react'
import { Button, Card, Space, Tooltip } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

export interface ListToolbarProps {
  /** 筛选项（Select / Segmented / RangePicker …），内部已 `Space wrap` */
  children: ReactNode
  /** 当前是否处于「有筛选」状态：为 true 才渲染「重置」按钮 */
  hasFilter?: boolean
  /** 重置回调（应同时清空输入态与查询参数，并把页码归 1） */
  onReset?: () => void
  /** 右侧区：提示文案或刷新按钮 */
  extra?: ReactNode
}

/**
 * 列表页筛选工具条。
 *
 * 抽的是这段在每个列表页重复三次的结构：
 *
 * ```tsx
 * <Card className="adm-toolbar" variant="outlined">
 *   <div className="adm-toolbar__row">
 *     <Space wrap size={12}>{filters}{hasFilter && <重置/>}</Space>
 *     {hint 或 刷新}
 *   </div>
 * </Card>
 * ```
 *
 * 约定：
 * - 重置按钮**只在有筛选时出现**（常驻会让人以为「重置」是主操作）；
 * - 重置必须同时清 `input` 与已提交的 `keyword`，只清一个会出现「输入框空了但还在按旧词过滤」；
 * - 换筛选要 `setPage(1)`，否则可能停留在越界页码上显示空表。
 */
export default function ListToolbar({ children, hasFilter, onReset, extra }: ListToolbarProps) {
  return (
    <Card className="adm-toolbar" variant="outlined">
      <div className="adm-toolbar__row">
        <Space wrap size={12}>
          {children}
          {hasFilter && onReset && (
            <Tooltip title="清空搜索与筛选条件">
              <Button type="text" size="small" icon={<ReloadOutlined />} onClick={onReset}>
                重置
              </Button>
            </Tooltip>
          )}
        </Space>
        {extra}
      </div>
    </Card>
  )
}
