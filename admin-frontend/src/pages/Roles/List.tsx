import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Drawer, Form, Input, Modal, Segmented, Space, Spin, Table, Tag, Tooltip, Tree, Typography } from 'antd'
import type { DataNode } from 'antd/es/tree'
import type { TableProps } from 'antd'
import { DeleteOutlined, EditOutlined, KeyOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import type { RbacPermissionGroup, RbacRole } from '../../types'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
/**
 * 角色管理：角色决定管理后台的菜单可见性与按钮级操作权限。
 *
 * 三个不可动摇的约束（改这块前先读）：
 * 1. **超级管理员是权限校验的逃逸通道**：不可删除、权限不可修改（后端也会拒绝）。
 * 2. **权限目录必须先就绪再渲染 Tree**：空 treeData 时 antd Tree 会把 checkedKeys 当成
 *    「不存在的 key」全部丢弃，表现为刚打开抽屉权限没勾上、保存后静默清空角色权限。
 * 3. **目录里没有的权限码要原样保留**：权限下线/改名后角色可能仍持有旧码，
 *    Tree 渲染不出它们，按勾选结果直接提交等于静默删除，故显式收集并回写。
 */

/** 权限分配树节点 key 前缀（模块分组伪节点，提交时剔除） */
const MODULE_KEY = 'module:'

/** 权限数 tooltip 里最多列几个码（58 个全列会糊成一屏） */
const TOOLTIP_CODE_LIMIT = 12

type TypeFilter = '' | 'system' | 'custom'

export default function RolesList() {
  const [roles, setRoles] = useState<RbacRole[]>([])
  const [loading, setLoading] = useState(true)

  // 筛选（角色数量不大，全量加载后前端过滤，不额外打接口）
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('')

  // 新建/编辑弹窗
  const [modalOpen, setModalOpen] = useState(false)
  const [editingRole, setEditingRole] = useState<RbacRole | null>(null)
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)

  // 权限分配抽屉
  const [permDrawerRole, setPermDrawerRole] = useState<RbacRole | null>(null)
  const [permissionTree, setPermissionTree] = useState<RbacPermissionGroup[]>([])
  const [checkedCodes, setCheckedCodes] = useState<string[]>([])
  const [permSaving, setPermSaving] = useState(false)
  // 点击「分配权限」到抽屉打开之间要等权限目录返回，期间给按钮一个 loading
  const [permOpeningId, setPermOpeningId] = useState<number | null>(null)
  // 目录只需拉一次；用 ref 而不是 permissionTree.length 判断，避免接口返回空数组时反复重试
  const permTreeLoaded = useRef(false)

  const loadRoles = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<{ items: RbacRole[] }>('/admin/rbac/roles')
      setRoles(data.items || [])
    } catch (err) {
      toast.error(getErrorMessage(err, '加载角色失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadRoles()
  }, [loadRoles])

  // ---- 统计与筛选（都基于已加载的全量列表） ----
  const stats = useMemo(() => {
    const systemCount = roles.filter((r) => r.is_system).length
    return {
      total: roles.length,
      system: systemCount,
      custom: roles.length - systemCount,
      covered: roles.reduce((sum, r) => sum + (r.user_count ?? 0), 0),
    }
  }, [roles])

  const visibleRoles = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    return roles.filter((r) => {
      if (typeFilter === 'system' && !r.is_system) return false
      if (typeFilter === 'custom' && r.is_system) return false
      if (!kw) return true
      return (
        r.name.toLowerCase().includes(kw) ||
        r.code.toLowerCase().includes(kw) ||
        (r.description ?? '').toLowerCase().includes(kw)
      )
    })
  }, [roles, typeFilter, keyword])

  const hasFilter = !!keyword.trim() || !!typeFilter

  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setTypeFilter('')
  }

  // 新建 / 编辑

  const openCreate = () => {
    setEditingRole(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (role: RbacRole) => {
    setEditingRole(role)
    form.setFieldsValue({ name: role.name, description: role.description ?? '' })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    // ⚠️ validateFields 必须包在 try 里：它校验失败时返回 rejected promise，
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
      if (editingRole) {
        await apiPut(`/admin/rbac/roles/${editingRole.id}`, {
          name: values.name,
          description: values.description || null,
        })
        toast.success('角色已更新')
      } else {
        await apiPost('/admin/rbac/roles', {
          code: values.code,
          name: values.name,
          description: values.description || null,
          permission_codes: [],
        })
        toast.success('角色创建成功，可在「分配权限」中配置')
      }
      setModalOpen(false)
      loadRoles()
    } catch (err) {
      toast.error(getErrorMessage(err, '操作失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = (role: RbacRole) => {
    Modal.confirm({
      title: `确认删除角色「${role.name}」`,
      content: (
        <>
          <div>删除后该角色下的 {role.user_count ?? 0} 个账号会立即失去这些权限。</div>
          <div style={{ marginTop: 4 }}>仍有用户使用的角色无法删除；内置角色不可删除。</div>
        </>
      ),
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiDelete(`/admin/rbac/roles/${role.id}`)
          toast.success('角色已删除')
          loadRoles()
        } catch (err) {
          toast.error(getErrorMessage(err, '删除失败'))
        }
      },
    })
  }

  // 权限分配

  /** 权限目录按需加载（首个角色打开时拉取，之后复用） */
  const ensurePermissionTree = async () => {
    if (permTreeLoaded.current) return true
    try {
      const data = await apiGet<{ items: RbacPermissionGroup[] }>('/admin/rbac/permissions')
      setPermissionTree(data.items || [])
      permTreeLoaded.current = true
      return true
    } catch (err) {
      toast.error(getErrorMessage(err, '加载权限目录失败'))
      return false
    }
  }

  /** 必须等权限目录返回后再打开抽屉：空 treeData 时先收 checkedKeys，antd Tree 会报
   *  missing keys 并全部丢弃（表现为刚打开时权限没勾上、保存会静默清空角色权限） */
  const openPermDrawer = async (role: RbacRole) => {
    setPermOpeningId(role.id)
    const ok = await ensurePermissionTree()
    setPermOpeningId(null)
    if (!ok) return
    setCheckedCodes(role.permissions || [])
    setPermDrawerRole(role)
  }

  /** 权限目录 → antd Tree 数据：模块分组节点 + 模块菜单权限 + 按钮权限 */
  const treeData: DataNode[] = useMemo(
    () =>
      permissionTree.map((g) => ({
        key: `${MODULE_KEY}${g.module}`,
        title: <strong>{g.name}</strong>,
        selectable: false,
        children: [
          ...(g.menu ? [{ key: g.menu.code, title: `${g.menu.name}（菜单）` }] : []),
          ...g.buttons.map((b) => ({ key: b.code, title: b.name })),
        ],
      })),
    [permissionTree],
  )

  /** 权限目录里的全部权限码 */
  const catalogCodes = useMemo(() => {
    const set = new Set<string>()
    for (const g of permissionTree) {
      if (g.menu) set.add(g.menu.code)
      for (const b of g.buttons) set.add(b.code)
    }
    return set
  }, [permissionTree])

  /** 角色持有但当前权限目录里没有的权限码（权限下线/改名后的遗留）：
   *  Tree 渲染不出它们，直接按勾选结果提交会被静默删除，故显式保留并提示 */
  const legacyCodes = useMemo(
    () => (permDrawerRole?.permissions || []).filter((c) => !catalogCodes.has(c)),
    [permDrawerRole, catalogCodes],
  )

  const handlePermSave = async () => {
    if (!permDrawerRole) return
    // 剔除模块分组伪节点 key，并补回目录里没有的遗留权限码（不静默丢弃）
    const codes = [
      ...new Set([...checkedCodes.filter((k) => !k.startsWith(MODULE_KEY)), ...legacyCodes]),
    ]
    setPermSaving(true)
    try {
      await apiPut(`/admin/rbac/roles/${permDrawerRole.id}/permissions`, {
        permission_codes: codes,
      })
      toast.success('角色权限已更新')
      setPermDrawerRole(null)
      loadRoles()
    } catch (err) {
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setPermSaving(false)
    }
  }

  const columns: TableProps<RbacRole>['columns'] = [
    {
      title: '角色名称',
      dataIndex: 'name',
      key: 'name',
      width: 170,
      render: (v: string) => <span className="adm-cell__name">{v}</span>,
    },
    {
      title: '编码',
      dataIndex: 'code',
      key: 'code',
      width: 150,
      render: (v: string) => (
        <Typography.Text code style={{ fontSize: 12 }}>
          {v}
        </Typography.Text>
      ),
    },
    {
      title: '说明',
      dataIndex: 'description',
      key: 'description',
      // 显式给宽度：不给的话这一列按内容撑开，整表比容器宽 2px，会凭空多出一条横向滚动条
      width: 340,
      render: (v: string | null) =>
        v ? (
          <Typography.Text ellipsis={{ tooltip: v }} style={{ maxWidth: 320 }}>
            {v}
          </Typography.Text>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '类型',
      dataIndex: 'is_system',
      key: 'is_system',
      width: 90,
      render: (isSystem: boolean) =>
        isSystem ? (
          <Tag color="blue" style={{ margin: 0 }}>
            内置
          </Tag>
        ) : (
          <Tag style={{ margin: 0 }}>自定义</Tag>
        ),
    },
    {
      title: '权限数',
      key: 'perm_count',
      width: 100,
      align: 'right',
      render: (_: unknown, record: RbacRole) => {
        const codes = record.permissions ?? []
        const head = codes.slice(0, TOOLTIP_CODE_LIMIT).join('、')
        const tip = codes.length
          ? `${head}${codes.length > TOOLTIP_CODE_LIMIT ? ` …等 ${codes.length} 项` : ''}`
          : '尚未分配任何权限'
        return (
          <Tooltip title={tip}>
            <span className="adm-num" style={{ cursor: 'help' }}>
              {codes.length}
            </span>
          </Tooltip>
        )
      },
    },
    {
      title: '用户数',
      dataIndex: 'user_count',
      key: 'user_count',
      width: 90,
      align: 'right',
      render: (v: number | undefined) => <span className="adm-num">{v ?? 0}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      fixed: 'right',
      render: (_: unknown, record: RbacRole) => (
        <Space size={0}>
          <PermGuard perm="role:edit">
            <Button
              type="link"
              size="small"
              icon={<KeyOutlined />}
              loading={permOpeningId === record.id}
              onClick={() => openPermDrawer(record)}
            >
              分配权限
            </Button>
          </PermGuard>
          <PermGuard perm="role:edit">
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
              编辑
            </Button>
          </PermGuard>
          {/* 内置角色（含超级管理员）不提供删除入口：后端也会拒绝，这里不给按钮免得白点一次 */}
          <PermGuard perm="role:delete">
            {!record.is_system && (
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleDelete(record)}
              >
                删除
              </Button>
            )}
          </PermGuard>
        </Space>
      ),
    },
  ]

  return (
    <div className="adm-page">
      <div className="adm-header">
        <div className="adm-header__main">
          <span className="adm-header__icon">
            <SafetyCertificateOutlined />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="adm-header__title">角色管理</div>
            <div className="adm-header__sub">
              <span>
                共 <strong>{stats.total}</strong> 个角色
              </span>
              <span className="adm-sep">·</span>
              <span>
                内置 <strong>{stats.system}</strong> · 自定义 <strong>{stats.custom}</strong>
              </span>
              <span className="adm-sep">·</span>
              <span>
                覆盖 <strong>{stats.covered}</strong> 个账号
              </span>
            </div>
          </div>
        </div>
        <div className="adm-header__actions">
          <Input.Search
            allowClear
            placeholder="搜索角色名称 / 编码 / 说明"
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            onSearch={(v) => setKeyword(v.trim())}
            style={{ width: 240 }}
          />
          <PermGuard perm="role:create">
            <Button type="primary" icon={<SafetyCertificateOutlined />} onClick={openCreate}>
              新建角色
            </Button>
          </PermGuard>
        </div>
      </div>

      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Segmented
              value={typeFilter}
              onChange={(v) => setTypeFilter(String(v) as TypeFilter)}
              options={[
                { value: '', label: '全部' },
                { value: 'system', label: '内置' },
                { value: 'custom', label: '自定义' },
              ]}
            />
            {hasFilter && (
              <Tooltip title="清空搜索与筛选">
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={handleResetFilters}>
                  重置
                </Button>
              </Tooltip>
            )}
          </Space>
          <Tooltip title="重新拉取角色与权限数">
            <Button icon={<ReloadOutlined />} onClick={loadRoles}>
              刷新
            </Button>
          </Tooltip>
        </div>
      </Card>

      <Card className="adm-tablecard" variant="outlined">
        <Table<RbacRole>
          className="adm-table"
          rowKey="id"
          columns={columns}
          dataSource={visibleRoles}
          loading={loading}
          scroll={{ x: 1180 }}
          pagination={false}
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的角色'
                emptyText='暂无角色'
                onReset={handleResetFilters}
              />
          ),
          }}
        />
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：业务角色为 admin 的账号登录后台后，按此处分配的角色权限渲染菜单与按钮；
        超级管理员拥有全部权限且不可删除、权限不可修改（它是权限校验的逃逸通道）。
      </Typography.Text>

      <Modal
        title={editingRole ? '编辑角色' : '新建角色'}
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
          <Form.Item
            name="code"
            label="角色编码"
            rules={[
              { required: !editingRole, message: '请输入角色编码' },
              { pattern: /^[a-z][a-z0-9_]*$/, message: '仅限小写字母开头，可含数字与下划线' },
            ]}
            extra={editingRole ? '编码创建后不可修改' : '如 content_admin（小写字母开头，可含数字与下划线）'}
          >
            <Input placeholder="content_admin" disabled={!!editingRole} maxLength={50} />
          </Form.Item>
          <Form.Item name="name" label="角色名称" rules={[{ required: true, message: '请输入角色名称' }]}>
            <Input placeholder="内容管理员" maxLength={50} />
          </Form.Item>
          <Form.Item name="description" label="角色说明">
            <Input.TextArea placeholder="该角色可管理的内容与边界" maxLength={200} rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={`分配权限 - ${permDrawerRole?.name ?? ''}`}
        size={420}
        open={!!permDrawerRole}
        onClose={() => setPermDrawerRole(null)}
        destroyOnHidden
        extra={
          <Space>
            <Button onClick={() => setPermDrawerRole(null)}>取消</Button>
            <Button
              type="primary"
              loading={permSaving}
              disabled={permDrawerRole?.code === 'super_admin'}
              onClick={handlePermSave}
            >
              保存
            </Button>
          </Space>
        }
      >
        {permDrawerRole?.code === 'super_admin' && (
          <Typography.Paragraph type="warning">
            超级管理员始终拥有全部权限，不可修改。
          </Typography.Paragraph>
        )}
        {legacyCodes.length > 0 && (
          // antd 6 的 Alert 用 title（message 已 deprecated，会打 console warning）
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title={`该角色持有 ${legacyCodes.length} 个已不在权限目录中的权限`}
            description={
              <>
                <div>{legacyCodes.join('、')}</div>
                <div style={{ marginTop: 4 }}>它们无法在下方勾选，但保存时会原样保留，不会被清除。</div>
              </>
            }
          />
        )}
        {permissionTree.length === 0 ? (
          // 双保险：目录未就绪时渲染 Tree 会让它把全部 checkedKeys 当成「不存在的 key」丢掉
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : (
          <Tree
            checkable
            defaultExpandAll
            treeData={treeData}
            checkedKeys={checkedCodes}
            onCheck={(keys) => setCheckedCodes(keys as string[])}
            disabled={permDrawerRole?.code === 'super_admin'}
          />
        )}
      </Drawer>
    </div>
  )
}
