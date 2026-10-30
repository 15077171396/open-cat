import type { ReactNode } from 'react'
import { Space } from 'antd'
import { useI18n } from '../../i18n'

export interface SelectionBarProps {
  /** 已选条数 */
  count: number
  /** 批量动作按钮 */
  children?: ReactNode
}

/**
 * 批量操作条：`已选 3 项  [批量发布] [批量删除]`。
 *
 * 只在 `count > 0` 时由调用方渲染（或在这里返回 null）——常驻会白占一行高度，
 * 而且「已选 0 项」的文案本身没有意义。
 *
 * ⚠️ 配套约定（现有代码已踩过坑，别回退）：
 * 换筛选 / 换页时必须清空已选，否则「在 A 页选中 3 条 → 改筛选 → 批量删除」
 * 会删掉当前看不见的行。清空逻辑由页面负责（`useCrudList` 的 setPage 不自动清），
 * 建议在筛选变更处统一调用 `setSelectedIds([])`。
 */
export default function SelectionBar({ count, children }: SelectionBarProps) {
  const { t } = useI18n()
  if (count <= 0) return null
  return (
    <Space size={8} wrap>
      <span className="adm-selinfo">
        {t('common.selectedPrefix')}
        <strong>{count}</strong>
        {t('common.selectedSuffix')}
      </span>
      {children}
    </Space>
  )
}
