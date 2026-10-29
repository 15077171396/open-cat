import { useEffect, useState } from 'react'
import { Form, Modal, Select, TreeSelect } from 'antd'
import { apiGet, apiPut } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { buildDeptTree } from '../../utils/dept'
import type { Department, RbacRole, User } from '../../types'

import { toast } from '../../utils/toast'
interface UserRbacModalProps {
  open: boolean
  user: User | null
  onCancel: () => void
  /** 保存成功后由父组件刷新列表与详情 */
  onSaved: () => void
}

/**
 * 用户 RBAC 分配弹窗：管理后台角色（多选）+ 部门归属（树选）。
 *
 * 仅业务角色为 admin 的账号会实际进入管理后台；
 * 对非 admin 账号分配角色仅作预置（升级为 admin 后即刻生效）。
 */
export default function UserRbacModal({ open, user, onCancel, onSaved }: UserRbacModalProps) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [roles, setRoles] = useState<RbacRole[]>([])
  const [departments, setDepartments] = useState<Department[]>([])

  // 角色与部门选项按需加载（弹窗首次打开时拉取，之后复用）
  useEffect(() => {
    if (!open) return
    apiGet<{ items: RbacRole[] }>('/admin/rbac/roles')
      .then((d) => setRoles(d.items || []))
      .catch(() => setRoles([]))
    apiGet<{ items: Department[] }>('/admin/rbac/departments')
      .then((d) => setDepartments(d.items || []))
      .catch(() => setDepartments([]))
  }, [open])

  // 打开时回显当前分配
  useEffect(() => {
    if (open && user) {
      form.setFieldsValue({
        role_ids: (user.rbac_roles || []).map((r) => r.id),
        department_id: user.department_id ?? undefined,
      })
    }
  }, [open, user, form])

  /** 部门平铺 → 树形选项（带层级缩进） */
  // 部门树：与另一个用户弹窗共用同一份实现（utils/dept.ts）
  const deptTreeData = buildDeptTree(departments)

  const handleSubmit = async () => {
    if (!user) return
    const values = await form.validateFields()
    setSaving(true)
    try {
      await apiPut(`/admin/users/${user.id}/rbac`, {
        role_ids: values.role_ids || [],
        // 0=清空归属；undefined=不变更
        department_id: values.department_id ?? 0,
      })
      toast.success('用户权限已更新')
      onSaved()
    } catch (err) {
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={`分配权限 - ${user?.account ?? ''}`}
      open={open}
      onOk={handleSubmit}
      onCancel={onCancel}
      confirmLoading={saving}
      okText="保存"
      cancelText="取消"
      zIndex={2000}
      destroyOnHidden
      forceRender
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          name="role_ids"
          label="管理后台角色"
          extra="角色决定该账号进入管理后台后的菜单与操作权限（业务角色为 admin 时生效）"
        >
          <Select
            mode="multiple"
            placeholder="选择一个或多个角色"
            options={roles.map((r) => ({
              value: r.id,
              label: `${r.name} (${r.code})`,
            }))}
            allowClear
          />
        </Form.Item>
        <Form.Item name="department_id" label="部门归属" extra="部门用于组织展示与筛选，不影响权限">
          <TreeSelect
            placeholder="不归属任何部门"
            treeData={deptTreeData}
            treeDefaultExpandAll
            allowClear
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
