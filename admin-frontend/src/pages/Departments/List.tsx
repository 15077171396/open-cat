import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Key } from 'react'
import { Button, Form, Input, InputNumber, Modal, Space, Tooltip, TreeSelect, Typography } from 'antd'
import type { TableProps } from 'antd'
import { ApartmentOutlined, DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import type { Department } from '../../types'

import { toast } from '../../utils/toast'
import PageHeader from '../../components/ui/PageHeader'
import { DataTable } from '../../components/ui'
/**
 * 部门管理：部门是用户的**组织归属**（在用户管理中分配），用于后台分组与筛选展示，
 * **不参与权限判定** —— 权限只看角色（角色管理）。
 *
 * 三个要点：
 * 1. 接口返回的是**平铺全量 + parent_id**，树在前端组装；服务端已按 sort_order 排好，
 *    组装时保持原顺序即可（排序值小的在前）。
 * 2. 「上级部门」下拉必须**排除自身及其整棵子树** —— 后端有防环校验会拒，
 *    但让用户选到一个必然报错的选项本身就是坏体验。
 * 3. 删除按钮在「有子部门 / 有归属用户」时直接置灰：后端同样会拒，先禁掉少一次白点。
 */

interface DeptRow extends Department {
  children?: DeptRow[]
}

/** 「上级部门」TreeSelect 的嵌套选项（递归类型必须显式声明，否则 children 会退化成 unknown[]） */
interface DeptTreeOption {
  value: number
  title: string
  children?: DeptTreeOption[]
}

export default function DepartmentsList() {
  const [departments, setDepartments] = useState<Department[]>([])
  const [loading, setLoading] = useState(true)

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  /** 受控展开：见下方 useEffect 注释（defaultExpandAllRows 在异步加载下会失效） */
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])

  const [modalOpen, setModalOpen] = useState(false)
  const [editingDept, setEditingDept] = useState<Department | null>(null)
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)

  const loadDepartments = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<{ items: Department[] }>('/admin/rbac/departments')
      setDepartments(data.items || [])
    } catch (err) {
      toast.error(getErrorMessage(err, '加载部门失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadDepartments()
  }, [loadDepartments])

  /**
   * 默认全部展开，且**在数据/搜索变化后重新展开**。
   *
   * 为什么不用 `expandable.defaultExpandAllRows`：它只在**首次渲染**时算一次，
   * 而首帧 dataSource 还是空数组（接口异步返回）→ 子部门会一直折叠着看不见，
   * 表现为「部门管理只显示顶级部门」（实测行数 1，实际 2）。改成受控 keys 才可靠。
   */
  useEffect(() => {
    setExpandedKeys(departments.map((d) => d.id))
  }, [departments, keyword])

  // ---- 平铺列表 → 嵌套树（保持服务端 sort_order 顺序） ----
  const treeRows: DeptRow[] = useMemo(() => {
    const byId = new Map<number, DeptRow>()
    departments.forEach((d) => byId.set(d.id, { ...d }))
    const roots: DeptRow[] = []
    byId.forEach((node) => {
      if (node.parent_id && byId.has(node.parent_id)) {
        const parent = byId.get(node.parent_id)!
        parent.children = parent.children || []
        parent.children.push(node)
      } else {
        roots.push(node)
      }
    })
    return roots
  }, [departments])

  /** 每个部门「含下级部门」的总人数（上级部门自己往往 0 人，只看本部门数会误判为空） */
  const subtreeUserCount = useMemo(() => {
    const map = new Map<number, number>()
    const sum = (node: DeptRow): number => {
      const own = node.user_count ?? 0
      const total = own + (node.children ?? []).reduce((acc, c) => acc + sum(c), 0)
      map.set(node.id, total)
      return total
    }
    treeRows.forEach(sum)
    return map
  }, [treeRows])

  const stats = useMemo(() => {
    const depth = (nodes: DeptRow[]): number =>
      nodes.length ? 1 + Math.max(...nodes.map((n) => depth(n.children ?? []))) : 0
    return {
      total: departments.length,
      covered: departments.reduce((s, d) => s + (d.user_count ?? 0), 0),
      depth: depth(treeRows),
    }
  }, [departments, treeRows])

  /** 搜索命中：命中节点的所有祖先也要显示（否则子部门命中却因为父级被过滤而消失） */
  const visibleRows: DeptRow[] = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    if (!kw) return treeRows
    const keep = (node: DeptRow): DeptRow | null => {
      const kids = (node.children ?? []).map(keep).filter((n): n is DeptRow => !!n)
      const selfHit = node.name.toLowerCase().includes(kw)
      if (!selfHit && kids.length === 0) return null
      return { ...node, children: kids.length ? kids : undefined }
    }
    return treeRows.map(keep).filter((n): n is DeptRow => !!n)
  }, [treeRows, keyword])

  // ---- 上级部门下拉：排除自身及整棵子树（后端防环校验会拒，先在这里挡掉） ----
  const excludedIds = useMemo(() => {
    if (!editingDept) return new Set<number>()
    const ids = new Set<number>([editingDept.id])
    const walk = (id: number) => {
      departments
        .filter((d) => d.parent_id === id)
        .forEach((d) => {
          ids.add(d.id)
          walk(d.id)
        })
    }
    walk(editingDept.id)
    return ids
  }, [editingDept, departments])

  const parentTreeData = useMemo(() => {
    const build = (nodes: DeptRow[]): DeptTreeOption[] =>
      nodes
        .filter((n) => !excludedIds.has(n.id))
        .map((n) => {
          const children = build(n.children ?? [])
          return { value: n.id, title: n.name, ...(children.length ? { children } : {}) }
        })
    return build(treeRows)
  }, [treeRows, excludedIds])

  // 新建 / 编辑 / 删除

  const openCreate = (presetParentId?: number) => {
    setEditingDept(null)
    form.resetFields()
    form.setFieldsValue({ sort_order: 0 })
    if (presetParentId) form.setFieldsValue({ parent_id: presetParentId })
    setModalOpen(true)
  }

  const openEdit = (dept: Department) => {
    setEditingDept(dept)
    form.setFieldsValue({
      name: dept.name,
      parent_id: dept.parent_id ?? undefined,
      sort_order: dept.sort_order,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    // ⚠️ validateFields 必须包在 try 里：校验失败它返回 rejected promise，
    // 直接 await 会让 Modal 的 onOk 抛未处理拒绝（控制台一条 pageerror，Sentry 会收）
    let values
    try {
      values = await form.validateFields()
    } catch (err) {
      if (isFormValidationError(err)) return
      throw err
    }
    setSubmitting(true)
    try {
      const payload = {
        name: values.name,
        parent_id: values.parent_id ?? null,
        sort_order: values.sort_order ?? 0,
      }
      if (editingDept) {
        await apiPut(`/admin/rbac/departments/${editingDept.id}`, payload)
        toast.success('部门已更新')
      } else {
        await apiPost('/admin/rbac/departments', payload)
        toast.success('部门创建成功')
      }
      setModalOpen(false)
      loadDepartments()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = (dept: DeptRow) => {
    Modal.confirm({
      title: `确认删除部门「${dept.name}」`,
      content: '有子部门或归属用户的部门无法删除；删除后该部门的归属关系一并消失。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiDelete(`/admin/rbac/departments/${dept.id}`)
          toast.success('部门已删除')
          loadDepartments()
        } catch (err) {
          toast.error(getErrorMessage(err, '删除失败'))
        }
      },
    })
  }

  /** 删除受限的原因（有则置灰按钮并说明，避免点一次吃错误） */
  const deleteBlockReason = (row: DeptRow): string | null => {
    const childCount = row.children?.length ?? 0
    const userCount = row.user_count ?? 0
    if (childCount > 0 && userCount > 0) return `有 ${childCount} 个子部门、${userCount} 名成员，无法删除`
    if (childCount > 0) return `有 ${childCount} 个子部门，无法删除`
    if (userCount > 0) return `有 ${userCount} 名成员，无法删除`
    return null
  }

  const columns: TableProps<DeptRow>['columns'] = [
    {
      title: '部门名称',
      dataIndex: 'name',
      key: 'name',
      render: (v: string) => <span className="adm-cell__name">{v}</span>,
    },
    {
      title: '排序值',
      dataIndex: 'sort_order',
      key: 'sort_order',
      width: 110,
      align: 'right',
      render: (v: number) => <span className="adm-num">{v}</span>,
    },
    {
      title: '人数',
      dataIndex: 'user_count',
      key: 'user_count',
      width: 120,
      align: 'right',
      render: (_: unknown, row: DeptRow) => {
        const own = row.user_count ?? 0
        const total = subtreeUserCount.get(row.id) ?? own
        return (
          <Tooltip title={`本部门 ${own} 人${total !== own ? ` · 含下级部门共 ${total} 人` : ''}`}>
            <span className="adm-num" style={{ cursor: 'help' }}>
              {own}
            </span>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 260,
      render: (_: unknown, row: DeptRow) => {
        const blocked = deleteBlockReason(row)
        return (
          <Space size={0}>
            <PermGuard perm="department:create">
              <Button type="link" size="small" icon={<PlusOutlined />} onClick={() => openCreate(row.id)}>
                子部门
              </Button>
            </PermGuard>
            <PermGuard perm="department:edit">
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>
                编辑
              </Button>
            </PermGuard>
            <PermGuard perm="department:delete">
              <Tooltip title={blocked ?? ''}>
                {/* 禁用的按钮不触发鼠标事件，Tooltip 需要一层可接收事件的包裹元素 */}
                <span>
                  <Button
                    type="link"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    disabled={!!blocked}
                    onClick={() => handleDelete(row)}
                  >
                    删除
                  </Button>
                </span>
              </Tooltip>
            </PermGuard>
          </Space>
        )
      },
    },
  ]

  return (
    <div className="adm-page">
      <PageHeader
        icon={<ApartmentOutlined />}
        title="部门管理"
        subtitle={[
            <>共 <strong>{stats.total}</strong> 个部门</>,
            <>最深 <strong>{stats.depth}</strong> 级</>,
            <>覆盖 <strong>{stats.covered}</strong> 个账号</>,
          ]}
        actions={
          <>
            <Input.Search
              allowClear
              placeholder="搜索部门名称"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onSearch={(v) => setKeyword(v.trim())}
              style={{ width: 220 }}
            />
            <Tooltip title="重新拉取部门与人数">
              <Button icon={<ReloadOutlined />} onClick={loadDepartments}>
                刷新
              </Button>
            </Tooltip>
            <PermGuard perm="department:create">
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreate()}>
                新建部门
              </Button>
            </PermGuard>

          </>
        }
      />

      <DataTable<DeptRow>
        rowKey="id"
        columns={columns}
        dataSource={visibleRows}
        loading={loading}
        scrollX={900}
        expandable={{
        expandedRowKeys: expandedKeys,
        onExpandedRowsChange: (keys) => setExpandedKeys([...keys]),
        childrenColumnName: 'children',
        }}
        empty={{ hasFilter: !!keyword, filteredText: "没有匹配的部门", emptyText: "还没有部门，先建一个吧", onReset: () => setKeywordInput('') }}
      />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：部门是用户的<b>组织归属</b>（在「用户管理」里分配），用于后台分组与筛选展示，
        <b>不参与权限判定</b> —— 能做什么只取决于角色。排序值小的排在前面，同父级下名称不可重复。
      </Typography.Text>

      <Modal
        title={editingDept ? '编辑部门' : '新建部门'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        confirmLoading={submitting}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
        forceRender
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="部门名称" rules={[{ required: true, message: '请输入部门名称' }]}>
            <Input placeholder="运营部" maxLength={50} />
          </Form.Item>
          <Form.Item
            name="parent_id"
            label="上级部门"
            extra="不选则为顶级部门；下拉里不会出现该部门自身及其下级"
          >
            <TreeSelect
              placeholder="不选则为顶级部门"
              treeDefaultExpandAll
              allowClear
              treeData={parentTreeData}
            />
          </Form.Item>
          <Form.Item name="sort_order" label="排序值" initialValue={0} extra="数值小的排在前面">
            <InputNumber min={0} max={10000} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
