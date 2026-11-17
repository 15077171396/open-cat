import type { ReactNode } from 'react'
import { Button, Space, Tooltip } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useI18n } from '../../i18n'

export interface ListToolbarProps {
  /** 筛选项（Select / RangePicker / Input …），内部已 `Space wrap` */
  children: ReactNode
  /** 当前是否处于「有筛选」状态：无筛选可清时重置按钮仍渲染但禁用 */
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
 * - 重置按钮**常驻**在筛选区末尾（普通描边按钮，与筛选控件同高），
 *   仅在没有可清条件时置灰，避免筛选控件左右横跳；
 * - 重置必须同时清 `input` 与已提交的 `keyword`，只清一个会出现「输入框空了但还在按旧词过滤」；
 * - 换筛选要 `setPage(1)`，否则可能停留在越界页码上显示空表。
 */
export default function ListToolbar({ children, hasFilter = false, onReset, extra }: ListToolbarProps) {
  const { t } = useI18n()
  return (
    <div className="adm-toolbar">
      <div className="adm-toolbar__row">
        <Space wrap size={12}>
          {children}
          {onReset && (
            <Tooltip title={hasFilter ? t('common.resetHint') : undefined}>
              <Button icon={<ReloadOutlined />} disabled={!hasFilter} onClick={onReset}>
                {t('common.reset')}
              </Button>
            </Tooltip>
          )}
        </Space>
        {/* 右侧区：页头操作区（PageHeaderActions 运行期挂到这里）与刷新/批量操作并列同一行 */}
        <div className="adm-toolbar__right">
          <div className="adm-toolbar__actions" />
          {extra}
        </div>
      </div>
    </div>
  )
}
