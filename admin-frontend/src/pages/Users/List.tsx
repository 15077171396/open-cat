import { useEffect, useRef, useState } from 'react'
import { Avatar, Button, Card, Input, Modal, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { DeleteOutlined, EyeOutlined, MinusCircleOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined, TeamOutlined, UndoOutlined, UserOutlined } from '@ant-design/icons'
import { apiDelete, apiGet, apiPost } from '../../api/client'
import { PermGuard } from '../../components/PermGuard'
import UserDetailDrawer, { AUTH_STATUS_MAP, formatDuration } from './UserDetailDrawer'
import UserEditModal from './UserEditModal'
import CreateUserModal from './CreateUserModal'
import UserRbacModal from './UserRbacModal'
import { ROLE_META } from './roleMeta'
import type { PointTransaction, User, UserRole } from '../../types'
import { getErrorMessage } from '../../utils/error'
import { formatDate } from '../../utils/datetime'
import { shouldRetreatOnePage } from '../../utils/pagination'
import { DEFAULT_PAGE_SIZE } from '../../constants'

import { toast } from '../../utils/toast'
import { TableEmpty } from '../../components/TableEmpty'
/** 角色筛选项（value 直接映射后端 role 字段） */
const ROLE_FILTER_OPTIONS = [
  { value: '', label: '全部角色' },
  { value: 'user', label: '普通用户' },
  { value: 'vip', label: 'VIP 用户' },
  { value: 'admin', label: '管理员' },
]

/**
 * 账号状态筛选项（value 直接映射后端 account_status）。
 * 默认 `active` —— 与历史行为一致，已注销账号必须主动切过去看，避免它们混进日常操作。
 */
const ACCOUNT_STATUS_OPTIONS = [
  { value: 'active', label: '正常账号' },
  { value: 'deleted', label: '已注销' },
  { value: 'all', label: '全部账号' },
]

export default function UsersList() {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  // 搜索：keywordInput 仅受控输入，回车/点击后才提交进查询参数
  // （避免未提交的输入文字意外影响删除/刷新等隐式 load() 的过滤条件）
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [role, setRole] = useState('')
  const [accountStatus, setAccountStatus] = useState('active')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [drawer, setDrawer] = useState<{ open: boolean; loading: boolean; user: User | null }>({
    open: false, loading: false, user: null,
  })
  // 用户积分流水（详情抽屉内展示最近记录）
  const [pointTxs, setPointTxs] = useState<PointTransaction[]>([])
  const [pointTxsLoading, setPointTxsLoading] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  // RBAC 分配弹窗（目标用户单独持有，避免与详情抽屉耦合）
  const [rbacTarget, setRbacTarget] = useState<User | null>(null)
  const [rbacOpen, setRbacOpen] = useState(false)
  // 批量选择与操作
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [batchLoading, setBatchLoading] = useState(false)

  // 请求序号防竞态：快速连续搜索/翻页时，晚到的旧响应不再覆盖新结果
  const loadSeqRef = useRef(0)

  /** 服务端分页加载；overrides 用于搜索/翻页时以新参数直接请求（state 异步更新不阻塞当次请求） */
  const load = async (overrides?: {
    page?: number
    keyword?: string
    role?: string
    accountStatus?: string
  }) => {
    const p = overrides?.page ?? page
    const k = overrides?.keyword ?? keyword
    const r = overrides?.role ?? role
    const s = overrides?.accountStatus ?? accountStatus
    const seq = ++loadSeqRef.current
    setLoading(true)
    try {
      const params: {
        page: number
        page_size: number
        keyword?: string
        role?: string
        account_status?: string
      } = {
        page: p,
        page_size: DEFAULT_PAGE_SIZE,
        account_status: s,
      }
      if (k.trim()) params.keyword = k.trim()
      if (r) params.role = r
      const data = await apiGet<{ items: User[]; total?: number }>('/admin/users', { params })
      if (seq !== loadSeqRef.current) return // 已有更新的请求在途，丢弃本次结果
      setUsers(data.items || [])
      setTotal(data.total ?? (data.items?.length ?? 0))
    } catch {
      if (seq !== loadSeqRef.current) return
      toast.error('加载用户列表失败')
    } finally {
      if (seq === loadSeqRef.current) setLoading(false)
    }
  }

  useEffect(() => { load({ page: 1 }) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearch = (value: string) => {
    setKeyword(value)
    // 换了筛选条件就把选择清掉：否则「选中 A 页的几条 → 改筛选 → 批量删除」会删掉看不见的行
    setSelectedIds([])
    setPage(1)
    load({ page: 1, keyword: value })
  }

  /** 清空全部筛选条件并回到第一页 */
  const handleResetFilters = () => {
    setKeywordInput('')
    setKeyword('')
    setRole('')
    setAccountStatus('active')
    setSelectedIds([])
    setPage(1)
    load({ page: 1, keyword: '', role: '', accountStatus: 'active' })
  }

  const handleFilterChange = (nextRole: string) => {
    setRole(nextRole)
    setSelectedIds([])
    setPage(1)
    load({ page: 1, role: nextRole })
  }

  /** 切换账号状态：换视图必须清空已选，否则「在正常视图选中 → 切到已注销 → 批量删除」会误伤 */
  const handleStatusChange = (next: string) => {
    setAccountStatus(next)
    setSelectedIds([])
    setPage(1)
    load({ page: 1, accountStatus: next })
  }

  const handlePageChange = (p: number) => {
    setSelectedIds([])
    setPage(p)
    load({ page: p })
  }

  const isDeletedView = accountStatus === 'deleted'
  const hasFilter = !!keyword.trim() || !!role || accountStatus !== 'active'

  const handleDelete = (u: User) => {
    Modal.confirm({
      title: '确认删除',
      content: `确认删除用户 ${u.account}?`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiDelete(`/admin/users/${u.id}`)
          toast.success('删除成功')
          load()
        } catch (err: unknown) {
          toast.error(getErrorMessage(err, '删除失败'))
        }
      },
    })
  }

  /**
   * 恢复已注销账号：只清软删除标记，积分/会员/收藏/学习记录全部保留。
   * 与「用户自己重新注册」的区别要写进确认文案 —— 后者会清空个人数据，两者不可混为一谈。
   */
  const handleRestore = (u: User) => {
    Modal.confirm({
      title: '恢复账号',
      content: `确认恢复用户 ${u.account}？恢复后该账号可重新登录，积分、会员与历史数据全部保留。`,
      okText: '恢复',
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost(`/admin/users/${u.id}/restore`)
          toast.success('已恢复')
          load()
        } catch (err: unknown) {
          toast.error(getErrorMessage(err, '恢复失败'))
        }
      },
    })
  }

  const handleBatchDelete = () => {
    Modal.confirm({
      title: '批量删除',
      content: `确认删除选中的 ${selectedIds.length} 个用户？采用软删除（与用户注销一致），关联数据保留。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchLoading(true)
        try {
          const data = await apiPost<{ deleted: number }>('/admin/users/batch-delete', {
            ids: selectedIds,
          })
          toast.success(`已删除 ${data.deleted} 个用户`)
          setSelectedIds([])
          // 当前页删空时回退一页，避免停留在越界空页
          if (shouldRetreatOnePage(total - data.deleted, page, DEFAULT_PAGE_SIZE)) {
            const p = page - 1
            setPage(p)
            load({ page: p })
          } else {
            load()
          }
        } catch (err: unknown) {
          toast.error(getErrorMessage(err, '批量删除失败'))
        } finally {
          setBatchLoading(false)
        }
      },
    })
  }

  /** 打开详情抽屉并拉取详情 + 最近积分流水 */
  const openDetail = async (u: User) => {
    setDrawer({ open: true, loading: true, user: null })
    setPointTxs([])
    try {
      const data = await apiGet<User>(`/admin/users/${u.id}`)
      setDrawer({ open: true, loading: false, user: data })
      // 并行拉取最近积分流水（失败不影响详情展示）
      setPointTxsLoading(true)
      apiGet<{ items: PointTransaction[] }>(`/admin/users/${u.id}/points`, { params: { page: 1, page_size: 10 } })
        .then((d) => setPointTxs(d?.items || []))
        .catch(() => setPointTxs([]))
        .finally(() => setPointTxsLoading(false))
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, '加载详情失败'))
      setDrawer({ open: false, loading: false, user: null })
    }
  }

  /** 编辑保存后刷新抽屉详情与列表（调整过积分时同步刷新抽屉内流水） */
  const handleEditSaved = async (pointsAdjusted: boolean) => {
    setEditOpen(false)
    const current = drawer.user
    if (current) {
      try {
        const data = await apiGet<User>(`/admin/users/${current.id}`)
        setDrawer({ open: true, loading: false, user: data })
        if (pointsAdjusted) {
          setPointTxsLoading(true)
          apiGet<{ items: PointTransaction[] }>(`/admin/users/${current.id}/points`, { params: { page: 1, page_size: 10 } })
            .then((d) => setPointTxs(d?.items || []))
            .catch(() => {})
            .finally(() => setPointTxsLoading(false))
        }
      } catch (err: unknown) {
        toast.error(getErrorMessage(err, '刷新详情失败'))
      }
    }
    load()
  }

  const columns: TableProps<User>['columns'] = [
    {
      title: '用户',
      key: 'user',
      width: 280,
      render: (_: unknown, u: User) => {
        const deleted = !!u.deleted_at
        return (
          <div className="adm-cell">
            <Avatar
              className="adm-avatar"
              size={40}
              icon={<UserOutlined />}
              src={u.avatar_url || undefined}
            />
            <div className="adm-cell__body">
              <div className="adm-cell__name" title={u.account}>
                {deleted ? <span className="adm-muted">{u.account}</span> : u.account}
                {deleted && (
                  <Tag color="default" style={{ marginLeft: 6 }}>
                    已注销
                  </Tag>
                )}
              </div>
              <div className="adm-cell__desc">
                ID {u.id}
                {deleted && u.deleted_at ? ` · 注销于 ${formatDate(u.deleted_at)}` : ''}
              </div>
            </div>
          </div>
        )
      },
    },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      width: 96,
      render: (v: UserRole) => {
        const meta = ROLE_META[v] || { text: v, color: 'default' }
        return <Tag color={meta.color}>{meta.text}</Tag>
      },
    },
    {
      title: '后台角色',
      key: 'rbac',
      width: 140,
      render: (_: unknown, u: User) =>
        u.rbac_roles?.length ? (
          <Space size={4} wrap>
            {u.rbac_roles.map((r) => (
              <Tag key={r.id} color="purple" style={{ margin: 0 }}>
                {r.name}
              </Tag>
            ))}
          </Space>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '部门',
      key: 'department',
      width: 110,
      render: (_: unknown, u: User) =>
        u.department_name ? (
          <span className="adm-muted">{u.department_name}</span>
        ) : (
          <span className="adm-muted">—</span>
        ),
    },
    {
      title: '星球认证',
      key: 'auth',
      width: 130,
      render: (_: unknown, u: User) => {
        const status = u.auth_status
        if (!status) {
          return (
            <span className="adm-muted">
              <MinusCircleOutlined /> 未申请
            </span>
          )
        }
        const cfg = AUTH_STATUS_MAP[status] || { color: 'default', text: status, icon: MinusCircleOutlined }
        const Icon = cfg.icon
        return (
          <Space size={4}>
            <Icon
              style={{
                color:
                  cfg.color === 'success' ? '#52c41a'
                    : cfg.color === 'processing' ? '#1677ff'
                      : cfg.color === 'error' ? '#ff4d4f' : '#bfbfbf',
              }}
            />
            <Tag color={cfg.color} style={{ margin: 0 }}>{cfg.text}</Tag>
          </Space>
        )
      },
    },
    {
      title: 'VIP 到期',
      dataIndex: 'vip_expire_time',
      key: 'vip_expire_time',
      width: 110,
      render: (v: string | null) =>
        v ? <span className="adm-muted">{v.slice(0, 10)}</span> : <span className="adm-muted">—</span>,
    },
    {
      title: '免费时长',
      key: 'free_used_seconds',
      width: 130,
      render: (_: unknown, u: User) => {
        if (u.role === 'vip' || u.role === 'admin') {
          return <span className="adm-muted">不限时</span>
        }
        const remaining = u.free_remaining_seconds ?? 0
        return (
          <Space orientation="vertical" size={0} style={{ lineHeight: 1.4 }}>
            <Typography.Text type={remaining === 0 ? 'danger' : undefined}>
              {formatDuration(u.free_used_seconds)}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {remaining === 0 ? '已耗尽' : `剩 ${formatDuration(remaining)}`}
            </Typography.Text>
          </Space>
        )
      },
    },
    {
      title: '积分',
      dataIndex: 'points',
      key: 'points',
      width: 96,
      align: 'right',
      render: (v: number | undefined) => (
        <span className="adm-metric">
          <Typography.Text strong>{v ?? 0}</Typography.Text>
        </span>
      ),
    },
    {
      title: '注册时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 110,
      render: (text: string | null) => <span className="adm-muted">{formatDate(text)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 210,
      fixed: 'right',
      render: (_: unknown, u: User) => {
        // 已注销账号只剩「恢复」一个动作：详情接口对它们返回 404，
        // 权限/删除也无意义（重复软删除被后端拒绝）
        if (u.deleted_at) {
          return (
            <PermGuard perm="user:restore">
              <Button type="link" size="small" icon={<UndoOutlined />} onClick={() => handleRestore(u)}>
                恢复
              </Button>
            </PermGuard>
          )
        }
        return (
          <Space size={0}>
            <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => openDetail(u)}>
              详情
            </Button>
            <PermGuard perm="user:edit">
              <Button
                type="link"
                size="small"
                icon={<SafetyCertificateOutlined />}
                onClick={() => {
                  setRbacTarget(u)
                  setRbacOpen(true)
                }}
              >
                权限
              </Button>
            </PermGuard>
            <PermGuard perm="user:delete">
              <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(u)}>
                删除
              </Button>
            </PermGuard>
          </Space>
        )
      },
    },
  ]

  return (
    <div className="adm-page">
      {/* 页头：查看权限即可见，只有「新增用户」按钮受 user:create 守卫 */}
      <div className="adm-header">
        <div className="adm-header__main">
          <span className="adm-header__icon">
            <TeamOutlined />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="adm-header__title">用户管理</div>
            <div className="adm-header__sub">
              <span>查看与维护账号、角色权限、会员与积分</span>
              <span className="adm-header__dot">·</span>
              <span>
                共 <strong>{total}</strong> 人
              </span>
            </div>
          </div>
        </div>
        <div className="adm-header__actions">
          <Input.Search
            placeholder="搜索账号"
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            onSearch={handleSearch}
            style={{ width: 220 }}
            allowClear
          />
          <PermGuard perm="user:create">
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新增用户
            </Button>
          </PermGuard>
        </div>
      </div>

      {/* 工具条：筛选 + 批量操作 */}
      <Card className="adm-toolbar" variant="outlined">
        <div className="adm-toolbar__row">
          <Space wrap size={12}>
            <Select
              value={role}
              onChange={handleFilterChange}
              options={ROLE_FILTER_OPTIONS}
              style={{ width: 150 }}
            />
            <Select
              value={accountStatus}
              onChange={handleStatusChange}
              options={ACCOUNT_STATUS_OPTIONS}
              style={{ width: 130 }}
            />
            {hasFilter && (
              <Tooltip title="清空关键词与筛选条件">
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={handleResetFilters}>
                  重置
                </Button>
              </Tooltip>
            )}
          </Space>
          <Space size={12}>
            {selectedIds.length > 0 && (
              <span className="adm-selinfo">
                已选 <strong>{selectedIds.length}</strong> 项
                <Button type="link" size="small" onClick={() => setSelectedIds([])}>
                  取消选择
                </Button>
              </span>
            )}
            {/* 已注销视图没有批量动作：已注销账号无法再删，恢复是逐条判断的决策 */}
            {!isDeletedView && (
              <PermGuard perm="user:delete">
                <Button
                  danger
                  icon={<DeleteOutlined />}
                  disabled={!selectedIds.length}
                  loading={batchLoading}
                  onClick={handleBatchDelete}
                >
                  批量删除
                </Button>
              </PermGuard>
            )}
          </Space>
        </div>
      </Card>

      {/* 列表 */}
      <Card className="adm-tablecard" variant="outlined">
        <Table<User>
          className="adm-table"
          dataSource={users}
          columns={columns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1412 }}
          rowSelection={
            isDeletedView
              ? undefined
              : {
                  selectedRowKeys: selectedIds,
                  onChange: (keys) => setSelectedIds(keys as number[]),
                }
          }
          locale={{
            emptyText: (
              <TableEmpty
                hasFilter={hasFilter}
                filteredText='没有符合筛选条件的用户'
                emptyText='还没有用户'
                onReset={handleResetFilters}
              />
          ),
          }}
          pagination={{
            current: page,
            pageSize: DEFAULT_PAGE_SIZE,
            total,
            onChange: handlePageChange,
            showTotal: (t) => `共 ${t} 条`,
            showSizeChanger: false,
          }}
        />
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        提示：删除为软删除 —— 账号标记为「已注销」并保留全部数据，可在「账号状态 → 已注销」中查看并恢复。
      </Typography.Text>

      <UserDetailDrawer
        open={drawer.open}
        loading={drawer.loading}
        user={drawer.user}
        pointTxs={pointTxs}
        pointTxsLoading={pointTxsLoading}
        onClose={() => setDrawer({ open: false, loading: false, user: null })}
        onEdit={() => setEditOpen(true)}
      />

      <CreateUserModal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false)
          // 新用户按注册时间倒序排在最前，回到第 1 页即可看到
          setPage(1)
          load({ page: 1 })
        }}
        onAccountDeleted={(account) => {
          // 建号撞到已注销账号：直接把人送到能处理它的地方（已注销视图 + 该账号的搜索结果），
          // 否则管理员只知道「不能复用」，却找不到那个账号
          setCreateOpen(false)
          setAccountStatus('deleted')
          setKeywordInput(account)
          setKeyword(account)
          setSelectedIds([])
          setPage(1)
          load({ page: 1, accountStatus: 'deleted', keyword: account })
          toast.info(`已切换到已注销账号，可直接恢复 ${account}`)
        }}
      />

      <UserEditModal
        open={editOpen}
        user={drawer.user}
        onCancel={() => setEditOpen(false)}
        onSaved={handleEditSaved}
      />

      <UserRbacModal
        open={rbacOpen}
        user={rbacTarget}
        onCancel={() => setRbacOpen(false)}
        onSaved={() => {
          setRbacOpen(false)
          // 列表行数据刷新后台角色/部门列；详情抽屉打开时同步刷新
          load()
          if (drawer.user && rbacTarget?.id === drawer.user.id) {
            apiGet<User>(`/admin/users/${rbacTarget.id}`)
              .then((data) => setDrawer({ open: true, loading: false, user: data }))
              .catch(() => {})
          }
        }}
      />
    </div>
  )
}
