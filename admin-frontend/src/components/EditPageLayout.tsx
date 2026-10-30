import type { ReactNode } from 'react'
import { Button, Card, Spin } from 'antd'
import { ArrowLeftOutlined, EditOutlined, SaveOutlined } from '@ant-design/icons'
import PageHeader from './ui/PageHeader'

interface EditPageLayoutProps {
  /** 页面标题，如 "编辑教程" / "新建教程" */
  title: string
  /** 返回按钮文案，如 "返回教程管理" */
  backLabel: string
  /** 数据加载中状态（编辑页拉取详情期间） */
  loading: boolean
  /** 保存按钮 loading 状态 */
  saving: boolean
  /** 取消回调（返回列表页） */
  onCancel: () => void
  /** 保存回调 */
  onSave: () => void
  /** 保存按钮文案，默认「保存」 */
  saveText?: string
  /** 表单内容 */
  children: ReactNode
}

/** 编辑页通用布局：页头（返回）+ 卡片（表单 + 底部操作栏），各 Edit 页共用 */
export default function EditPageLayout({
  title,
  backLabel,
  loading,
  saving,
  onCancel,
  onSave,
  saveText = '保存',
  children,
}: EditPageLayoutProps) {
  return (
    <div>
      <PageHeader
        icon={<EditOutlined />}
        title={title}
        actions={
          <Button icon={<ArrowLeftOutlined />} onClick={onCancel}>
            {backLabel}
          </Button>
        }
      />

      <Spin spinning={loading} size="large">
        <Card>
          {children}
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 12,
              paddingTop: 16,
              borderTop: '1px solid var(--app-border-light)',
            }}
          >
            <Button onClick={onCancel}>取消</Button>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={onSave}>
              {saveText}
            </Button>
          </div>
        </Card>
      </Spin>
    </div>
  )
}
