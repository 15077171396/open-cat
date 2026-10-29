import { useEffect, useState } from 'react'
import { Form, Input, Modal, Select, TreeSelect } from 'antd'
import { apiGet, apiPost } from '../../api/client'
import { getErrorData, getErrorMessage } from '../../utils/error'
import { buildDeptTree } from '../../utils/dept'
import type { Department, RbacRole } from '../../types'

import { toast } from '../../utils/toast'
interface CreateFormValues {
  account: string
  password: string
  role: string
  rbac_role_ids?: number[]
  department_id?: number
}

interface CreateUserModalProps {
  open: boolean
  onCancel: () => void
  /** 创建成功后由父组件关闭弹窗并刷新列表（新用户按注册时间倒序排在最前） */
  onCreated: () => void
  /**
   * 账号已被注销时的引导回调：后端 409 + `data.reason='account_deleted'`。
   * 父组件据此切到「已注销」视图并搜索该账号 —— 否则管理员只会看到
   * 「不能复用」四个字，却不知道该账号在哪、怎么处理。
   */
  onAccountDeleted?: (account: string) => void
}

/** 新增用户弹窗：账号 + 初始密码 + 业务角色 + RBAC 角色与部门（admin 角色时生效） */
export default function CreateUserModal({
  open,
  onCancel,
  onCreated,
  onAccountDeleted,
}: CreateUserModalProps) {
  const [form] = Form.useForm<CreateFormValues>()
  const [creating, setCreating] = useState(false)
  const [roles, setRoles] = useState<RbacRole[]>([])
  const [departments, setDepartments] = useState<Department[]>([])

  // RBAC 角色与部门选项按需加载（弹窗首次打开时拉取，之后复用）
  useEffect(() => {
    if (!open) return
    apiGet<{ items: RbacRole[] }>('/admin/rbac/roles')
      .then((d) => setRoles(d.items || []))
      .catch(() => setRoles([]))
    apiGet<{ items: Department[] }>('/admin/rbac/departments')
      .then((d) => setDepartments(d.items || []))
      .catch(() => setDepartments([]))
  }, [open])

  const handleCreateSubmit = async () => {
    let values: CreateFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    setCreating(true)
    try {
      await apiPost('/admin/users', {
        account: values.account.trim(),
        password: values.password,
        role: values.role,
        rbac_role_ids: values.rbac_role_ids || [],
        department_id: values.department_id ?? null,
      })
      toast.success('用户创建成功')
      onCreated()
    } catch (err: unknown) {
      // 409 有两种原因，处理方式完全不同：
      // 「账号已存在」→ 换个账号即可；「账号已注销」→ 需要去已注销列表恢复。
      // 判别走后端给的 data.reason，不匹配 message 文本（文案一改就静默失效）。
      const data = getErrorData<{ reason?: string; account?: string }>(err)
      if (data?.reason === 'account_deleted') {
        const account = data.account || values.account.trim()
        Modal.confirm({
          title: '该账号已被注销',
          content: (
            <>
              <div>账号 <strong>{account}</strong> 已被注销，不能直接新建（会与历史数据冲突）。</div>
              <div style={{ marginTop: 8 }}>
                如需让该用户回来，可到「账号状态 → 已注销」中恢复 ——
                恢复后积分、会员与历史数据全部保留。
              </div>
            </>
          ),
          okText: '去恢复',
          cancelText: '取消',
          // 父弹窗 zIndex 是 2000，确认框必须更高，否则被盖在下面
          zIndex: 2100,
          onOk: () => onAccountDeleted?.(account),
        })
        return
      }
      toast.error(getErrorMessage(err, '创建失败'))
    } finally {
      setCreating(false)
    }
  }

  /** 部门平铺 → 树形选项 */
  // 部门树：与另一个用户弹窗共用同一份实现（utils/dept.ts）
  const deptTreeData = buildDeptTree(departments)

  return (
    <Modal
      title="新增用户"
      open={open}
      onOk={handleCreateSubmit}
      onCancel={onCancel}
      confirmLoading={creating}
      okText="创建"
      cancelText="取消"
      zIndex={2000}
      destroyOnHidden
      forceRender
    >
      <Form form={form} layout="vertical" preserve={false} initialValues={{ role: 'user' }} style={{ marginTop: 16 }}>
        <Form.Item
          name="account"
          label="账号"
          rules={[
            { required: true, message: '请输入账号' },
            {
              // 与注册端口径一致：手机号或邮箱
              pattern: /^(1[3-9]\d{9}|[\w.+-]+@[\w-]+\.[\w.-]+)$/,
              message: '请输入正确的手机号或邮箱',
            },
          ]}
        >
          {/* autoComplete="off"：这里填的是**新用户**的账号，不是当前管理员的。
              若按 Chrome 的通用建议标成 username，浏览器会把管理员自己的账号填进来 */}
          <Input placeholder="手机号或邮箱" maxLength={200} allowClear autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="password"
          label="初始密码"
          rules={[
            { required: true, message: '请输入初始密码' },
            { min: 6, max: 50, message: '密码长度 6-50 位' },
          ]}
        >
          <Input.Password placeholder="6-50 位" maxLength={50} allowClear autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="role"
          label="业务角色"
          rules={[{ required: true, message: '请选择业务角色' }]}
          extra="业务角色决定前台权益（VIP 免费读/不限时等）；进入管理后台需选择 admin"
        >
          <Select
            options={[
              { value: 'user', label: '普通用户 (user)' },
              { value: 'vip', label: 'VIP 用户 (vip)' },
              { value: 'admin', label: '管理员 (admin)' },
            ]}
          />
        </Form.Item>
        <Form.Item
          noStyle
          shouldUpdate={(prev, cur) => prev.role !== cur.role}
        >
          {({ getFieldValue }) => {
            const isAdmin = getFieldValue('role') === 'admin'
            return (
              <>
                <Form.Item
                  name="rbac_role_ids"
                  label="管理后台角色"
                  extra={isAdmin ? '角色决定该账号登录管理后台后的菜单与操作权限' : '仅业务角色为 admin 时生效（可预选）'}
                >
                  <Select
                    mode="multiple"
                    placeholder="选择一个或多个后台角色"
                    options={roles.map((r) => ({
                      value: r.id,
                      label: `${r.name} (${r.code})`,
                    }))}
                    allowClear
                  />
                </Form.Item>
                <Form.Item name="department_id" label="部门归属">
                  <TreeSelect
                    placeholder="不归属任何部门"
                    treeData={deptTreeData}
                    treeDefaultExpandAll
                    allowClear
                  />
                </Form.Item>
              </>
            )
          }}
        </Form.Item>
      </Form>
    </Modal>
  )
}
