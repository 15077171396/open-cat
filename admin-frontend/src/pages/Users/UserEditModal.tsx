import { useEffect, useRef, useState } from 'react'
import { Form, Input, Modal, Select } from 'antd'
import { apiPut } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { isSafeUrl } from '../../utils/url'
import type { User } from '../../types'

import { toast } from '../../utils/toast'
interface EditFormValues {
  role: string
  avatar_url: string
}

interface UserEditModalProps {
  open: boolean
  user: User | null
  onCancel: () => void
  /** 保存成功后由父组件刷新详情与列表 */
  onSaved: () => void
}

/** 编辑用户信息弹窗：角色 / 头像 */
export default function UserEditModal({ open, user, onCancel, onSaved }: UserEditModalProps) {
  const [form] = Form.useForm<EditFormValues>()
  const [saving, setSaving] = useState(false)
  // 回填标记：仅在弹窗打开且目标用户变化时回填，避免详情刷新（user 引用更新）覆盖编辑中的表单
  const filledUserIdRef = useRef<number | null>(null)

  useEffect(() => {
    if (open && user && filledUserIdRef.current !== user.id) {
      filledUserIdRef.current = user.id
      form.setFieldsValue({
        role: user.role,
        avatar_url: user.avatar_url || '',
      })
    }
    if (!open) filledUserIdRef.current = null
  }, [open, user, form])

  const handleSave = async () => {
    if (!user) return
    let values: EditFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    // 安全：头像 URL 仅允许 http/https 协议（与封面图校验标准一致）
    const avatarUrl = (values.avatar_url || '').trim()
    if (avatarUrl && !isSafeUrl(avatarUrl)) {
      toast.error('头像 URL 必须以 http:// 或 https:// 开头')
      return
    }
    setSaving(true)
    try {
      await apiPut(`/admin/users/${user.id}`, {
        role: values.role,
        avatar_url: avatarUrl,
      })
      toast.success('用户信息已更新')
      onSaved()
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, '更新失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="编辑用户信息"
      open={open}
      onOk={handleSave}
      onCancel={onCancel}
      confirmLoading={saving}
      okText="保存"
      cancelText="取消"
      zIndex={2000}
      destroyOnHidden
      forceRender
    >
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="role"
          label="角色"
          rules={[{ required: true, message: '请选择角色' }]}
        >
          <Select
            options={[
              { value: 'user', label: '普通用户 (user)' },
              { value: 'admin', label: '管理员 (admin)' },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="avatar_url"
          label="头像 URL"
          extra="留空表示清除头像"
        >
          <Input placeholder="https://..." allowClear />
        </Form.Item>
      </Form>
    </Modal>
  )
}
