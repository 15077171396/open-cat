import type { ReactNode } from 'react'
import { Button, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'

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

/** 列表页通用头部：图标 + 标题 + 右侧操作区（新建按钮/搜索框等） */
export default function ListPageHeader({
  icon,
  title,
  onCreate,
  createLabel = '新建',
  extra,
}: ListPageHeaderProps) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {icon} {title}
      </Typography.Title>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {extra}
        {onCreate && (
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
            {createLabel}
          </Button>
        )}
      </div>
    </div>
  )
}
