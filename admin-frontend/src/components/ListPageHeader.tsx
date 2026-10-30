import type { ReactNode } from 'react'
import { Button } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import PageHeader from './ui/PageHeader'

interface ListPageHeaderProps {
  /** 标题前的图标节点 */
  icon: ReactNode
  /** 页面标题，如 "教程管理" */
  title: string
  /** 新建按钮回调，不传则不显示新建按钮 */
  onCreate?: () => void
  /** 新建按钮文案，默认 "新建" */
  createLabel?: string
  /** 右侧额外操作区（如搜索框），与新建按钮并排 */
  extra?: ReactNode
}

/** 旧版列表页头：保留 API，内部统一走 PageHeader，避免两套页头视觉分叉 */
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
