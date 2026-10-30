import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Avatar, Button, Descriptions, Form, Input, Skeleton, Space, Table, Tag, Typography, Upload } from 'antd'
import type { UploadProps } from 'antd'
import { UploadOutlined, UserOutlined } from '@ant-design/icons'
import { apiGet, apiPut } from '../../api/client'
import ListPageHeader from '../../components/ListPageHeader'
import { SettingsNav, SettingsPanel } from '../../components/settings/SettingsNav'
import { DEFAULT_PAGE_SIZE } from '../../constants'
import { useAuthStore } from '../../store/authStore'
import { getErrorMessage } from '../../utils/error'
import { formatDateTime } from '../../utils/datetime'
import { uploadAvatar } from '../../utils/upload'
import SecurityTab from './SecurityTab'

import { toast } from '../../utils/toast'
/**
 * 后台个人中心（2026-10-13）。
 *
 * 布局与系统设置一致：**左侧分类导航 + 右侧单面板**（2026-10-27 由横排 tab 改来）。
 * 五个面板：
 *   ① 账号信息 —— 头像/昵称/简介可改，账号/角色/部门/注册时间只读
 *   ② 修改密码 —— 改完**当前会话会被吊销**（后端递增令牌版本号），必须重新登录
 *   ③ 安全设置 —— 二次验证（TOTP）与退出全部设备
 *   ④ 我的操作记录 —— 复用 `/admin/audit-logs?admin_id=<自己>`
 *   ⑤ 我的权限 —— 角色 / 部门 / 权限码（按模块分组，名称取自权限目录）
 *
 * ⚠️ 本页**不挂 RBAC 页面级守卫**：个人中心属于「任何已登录管理员都该能进」的页面，
 *    与具体业务权限无关（路由上只在 `<RequireAdmin>` 之内）。
 */

/** GET /users/profile（UserInfoData 的子集，本页用到的字段） */
interface MyProfile {
  id: number
  account: string
  role: string
  nickname?: string | null
  bio?: string | null
  avatar_url?: string | null
  created_at?: string | null
}

/** GET /admin/rbac/profile */
interface RbacProfile {
  user: { id: number; account: string; nickname: string | null; avatar_url: string | null }
  roles: { id: number; code: string; name: string }[]
  permissions: string[]
  is_super: boolean
  department: { id: number; name: string } | null
}

/** GET /admin/rbac/permissions（权限目录，用于把权限码翻成中文名） */
interface PermissionGroup {
  module: string
  name: string
  menu: { code: string; name: string } | null
  buttons: { code: string; name: string }[]
}

/** GET /admin/audit-logs 的 items 元素（本页只展示其中几列） */
interface AuditLogItem {
  id: number
  action: string
  action_label: string
  target_type: string
  target_id: number | null
  summary: string
  ip: string | null
  created_at: string | null
}

interface AuditLogResponse {
  items: AuditLogItem[]
  total: number
  page: number
  page_size: number
}

/** 角色英文码 → 中文名（与用户管理页口径一致；RBAC 角色另有 roles 字段单独展示） */
const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  vip: '会员',
  user: '普通用户',
}

/** 面板清单：key / 左导航文案 / 标题 / 说明 / 内容 */
const PANELS: { key: string; label: string; title: string; description: string; render: () => JSX.Element }[] = [
  {
    key: 'account',
    label: '账号信息',
    title: '账号信息',
    description: '账号用于登录、不可自助修改；昵称与简介会显示在前台与操作审计里。',
    render: () => <AccountTab />,
  },
  {
    key: 'password',
    label: '修改密码',
    title: '修改密码',
    description: '改完后当前登录立即失效，需要用新密码重新登录。',
    render: () => <PasswordTab />,
  },
  {
    key: 'security',
    label: '安全设置',
    title: '安全设置',
    description: '二次验证（TOTP）与登录设备管理——密码之外的第二道门，以及「现在有谁在登着我的号」。',
    render: () => <SecurityTab />,
  },
  {
    key: 'activity',
    label: '我的操作记录',
    title: '我的操作记录',
    description: '只列出你自己的管理操作，数据来自操作审计。',
    render: () => <ActivityTab />,
  },
  {
    key: 'permissions',
    label: '我的权限',
    title: '我的权限',
    description: '当前角色、部门与已授予的权限码；超级管理员校验时直接放行。',
    render: () => <PermissionTab />,
  },
]

const NAV_CATEGORIES = [
  { key: 'account', label: '账号', items: PANELS.slice(0, 3).map((p) => ({ key: p.key, label: p.label })) },
  { key: 'records', label: '记录与权限', items: PANELS.slice(3).map((p) => ({ key: p.key, label: p.label })) },
]

export default function ProfilePage() {
  const [activeKey, setActiveKey] = useState(PANELS[0].key)
  /**
   * 已经打开过的面板。
   *
   * 为什么不像条件渲染那样「切走就卸载」：账号信息/修改密码都有表单，
   * 切去别的面板再回来会把未保存的输入清空 —— antd Tabs 默认也不卸载，
   * 这里保持同样的行为（未访问过的面板仍然不挂载，省掉无谓的请求）。
   */
  const [opened, setOpened] = useState<string[]>([PANELS[0].key])

  const select = (key: string) => {
    setActiveKey(key)
    setOpened((prev) => (prev.includes(key) ? prev : [...prev, key]))
  }

  const current = PANELS.find((p) => p.key === activeKey) ?? PANELS[0]

  return (
    <div>
      <ListPageHeader
        icon={<UserOutlined />}
        title="个人中心"
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            账号资料、密码与本人权限；修改密码后需要重新登录
          </Typography.Text>
        }
      />

      <div className="settings-layout">
        <SettingsNav
          categories={NAV_CATEGORIES}
          activeKey={activeKey}
          onSelect={select}
          ariaLabel="个人中心分类"
        />

        <SettingsPanel title={current.title} description={current.description}>
          {opened.map((key) => {
            const panel = PANELS.find((p) => p.key === key)
            if (!panel) return null
            return (
              <div key={key} style={{ display: key === activeKey ? undefined : 'none' }}>
                {panel.render()}
              </div>
            )
          })}
        </SettingsPanel>
      </div>
    </div>
  )
}

// ========== ① 账号信息 ==========

function AccountTab() {
  const [form] = Form.useForm<{ nickname: string; bio: string }>()
  const [profile, setProfile] = useState<MyProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [avatarUploading, setAvatarUploading] = useState(false)
  const fetchUser = useAuthStore((s) => s.fetchUser)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<MyProfile>('/users/profile')
      setProfile(data)
      form.setFieldsValue({ nickname: data.nickname || '', bio: data.bio || '' })
    } catch (err) {
      toast.error(getErrorMessage(err, '加载账号信息失败'))
    } finally {
      setLoading(false)
    }
  }, [form])

  useEffect(() => {
    load()
  }, [load])

  // 头像上传：后端 /users/avatar 会直接落库，这里只需刷新展示与顶栏头像
  const handleAvatarUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options
    setAvatarUploading(true)
    try {
      const result = await uploadAvatar(file as File)
      setProfile((p) => (p ? { ...p, avatar_url: result.avatar_url } : p))
      onSuccess?.(result, file as File)
      toast.success('头像更新成功')
      // 顶栏头像读的是 authStore，必须重新拉一次才会变
      await fetchUser()
    } catch (err) {
      toast.error(getErrorMessage(err, '头像上传失败'))
      onError?.(err instanceof Error ? err : new Error('上传失败'))
    } finally {
      setAvatarUploading(false)
    }
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      await apiPut('/users/profile', {
        nickname: values.nickname?.trim() || '',
        bio: values.bio?.trim() || '',
      })
      toast.success('资料已更新')
      await Promise.all([load(), fetchUser()])
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return // 表单校验失败
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <Space align="center" size={20} style={{ marginBottom: 24 }}>
        <Avatar
          size={72}
          src={profile?.avatar_url || undefined}
          style={{
            // 有头像时用中性浅底：品牌紫只服务于「无头像时的首字母兜底」，
            // 否则会透过透明 PNG 露出来（图标类头像多为透明底，会染成一块紫底）。
            background: profile?.avatar_url ? '#f5f7fa' : '#4f46e5',
            border: profile?.avatar_url ? '1px solid var(--app-border-light)' : 'none',
            fontSize: 28,
          }}
        >
          {profile?.avatar_url
            ? null
            : (profile?.nickname || profile?.account || '?').slice(0, 1).toUpperCase()}
        </Avatar>
        <div>
          <Upload
            accept="image/jpeg,image/png,image/webp,image/gif"
            showUploadList={false}
            customRequest={handleAvatarUpload}
          >
            <Button icon={<UploadOutlined />} loading={avatarUploading}>
              {profile?.avatar_url ? '更换头像' : '上传头像'}
            </Button>
          </Upload>
          <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 6 }}>
            支持 JPG / PNG / WebP / GIF，≤5MB
          </Typography.Text>
        </div>
      </Space>

      <Form form={form} layout="vertical" disabled={loading}>
        <Form.Item label="账号" extra="账号用于登录，暂不支持自助修改">
          <Input value={profile?.account || ''} disabled />
        </Form.Item>
        <Form.Item
          label="昵称"
          name="nickname"
          rules={[{ max: 50, message: '昵称不超过 50 个字符' }]}
        >
          <Input placeholder="用于前台展示与操作审计" maxLength={50} />
        </Form.Item>
        <Form.Item label="简介" name="bio" rules={[{ max: 200, message: '简介不超过 200 个字符' }]}>
          <Input.TextArea rows={3} placeholder="一句话介绍自己" maxLength={200} showCount />
        </Form.Item>
      </Form>

      <Descriptions column={2} size="small" style={{ marginBottom: 16 }}>
        <Descriptions.Item label="角色">
          <Tag color="blue">{ROLE_LABELS[profile?.role || ''] || profile?.role || '-'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="注册时间">
          {formatDateTime(profile?.created_at)}
        </Descriptions.Item>
      </Descriptions>

      <Button type="primary" loading={saving} onClick={handleSave}>
        保存资料
      </Button>
    </div>
  )
}

// ========== ② 修改密码 ==========

function PasswordTab() {
  const [form] = Form.useForm<{ old_password: string; new_password: string; confirm: string }>()
  const [saving, setSaving] = useState(false)
  const navigate = useNavigate()
  const logout = useAuthStore((s) => s.logout)

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      await apiPut('/users/password', {
        old_password: values.old_password,
        new_password: values.new_password,
      })
      // ⚠️ 后端改密后会递增令牌版本号并删除 refresh_token —— 当前会话立即失效。
      // 这里必须主动登出跳登录页，否则后续每个请求都 401，用户只会看到一片报错。
      toast.success('密码修改成功，请用新密码重新登录')
      form.resetFields()
      await logout()
      navigate('/login')
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return
      toast.error(getErrorMessage(err, '修改密码失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ maxWidth: 420 }}>
      <Form form={form} layout="vertical">
        <Form.Item
          label="当前密码"
          name="old_password"
          rules={[{ required: true, message: '请输入当前密码' }]}
        >
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          label="新密码"
          name="new_password"
          rules={[
            { required: true, message: '请输入新密码' },
            { min: 6, message: '新密码至少 6 位' },
          ]}
          extra="至少 6 位；改完后当前登录会失效，需要用新密码重新登录"
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          label="确认新密码"
          name="confirm"
          dependencies={['new_password']}
          rules={[
            { required: true, message: '请再次输入新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('new_password') === value) return Promise.resolve()
                return Promise.reject(new Error('两次输入的密码不一致'))
              },
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
      <Button type="primary" loading={saving} onClick={handleSubmit}>
        确认修改
      </Button>
    </div>
  )
}

// ========== ③ 我的操作记录 ==========

function ActivityTab() {
  const myId = useAuthStore((s) => s.user?.id)
  const [items, setItems] = useState<AuditLogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  // 请求序号：翻页过快时旧响应可能后到，只允许最新一次落盘
  const seqRef = useRef(0)

  const load = useCallback(async () => {
    if (!myId) return
    const seq = ++seqRef.current
    setLoading(true)
    try {
      const data = await apiGet<AuditLogResponse>('/admin/audit-logs', {
        // 只筛自己：后端已支持 admin_id 过滤，无需新增接口
        params: { page, page_size: DEFAULT_PAGE_SIZE, admin_id: myId },
      })
      if (seq !== seqRef.current) return
      setItems(data.items || [])
      setTotal(data.total ?? 0)
    } catch (err) {
      if (seq !== seqRef.current) return
      toast.error(getErrorMessage(err, '加载操作记录失败'))
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [myId, page])

  useEffect(() => {
    load()
  }, [load])

  return (
    <Table<AuditLogItem>
      rowKey="id"
      loading={loading}
      dataSource={items}
      size="small"
      columns={[
        {
          title: '时间',
          dataIndex: 'created_at',
          width: 170,
          render: (v: string | null) => <Typography.Text style={{ fontSize: 13 }}>{formatDateTime(v)}</Typography.Text>,
        },
        {
          title: '动作',
          dataIndex: 'action_label',
          width: 120,
          render: (v: string, row) => <Tag color="blue">{v || row.action}</Tag>,
        },
        {
          title: '目标',
          dataIndex: 'target_type',
          width: 120,
          render: (v: string, row) => (v ? `${v}${row.target_id ? ` #${row.target_id}` : ''}` : '-'),
        },
        { title: '摘要', dataIndex: 'summary', ellipsis: true },
        { title: 'IP', dataIndex: 'ip', width: 140, render: (v: string | null) => v || '-' },
      ]}
      pagination={{
        current: page,
        pageSize: DEFAULT_PAGE_SIZE,
        total,
        showSizeChanger: false,
        onChange: setPage,
      }}
      locale={{ emptyText: '暂无操作记录' }}
    />
  )
}

// ========== ④ 我的权限 ==========

function PermissionTab() {
  const [rbac, setRbac] = useState<RbacProfile | null>(null)
  const [catalog, setCatalog] = useState<PermissionGroup[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        // 两个接口并发：档案给「我有什么」，目录给「这些码叫什么」
        const [profile, groups] = await Promise.all([
          apiGet<RbacProfile>('/admin/rbac/profile'),
          apiGet<{ items: PermissionGroup[] }>('/admin/rbac/permissions'),
        ])
        if (cancelled) return
        setRbac(profile)
        setCatalog(groups.items || [])
      } catch (err) {
        if (!cancelled) toast.error(getErrorMessage(err, '加载权限信息失败'))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  const mine = new Set(rbac?.permissions || [])
  // 权限码 → 中文名（目录里没有的码（如后台新增未同步）原样显示，不隐藏）
  const nameOf = new Map<string, string>()
  catalog.forEach((g) => {
    if (g.menu) nameOf.set(g.menu.code, g.menu.name)
    g.buttons.forEach((b) => nameOf.set(b.code, b.name))
  })

  const granted = catalog
    .map((g) => ({
      module: g.module,
      name: g.name,
      codes: [
        ...(g.menu && mine.has(g.menu.code) ? [g.menu.code] : []),
        ...g.buttons.filter((b) => mine.has(b.code)).map((b) => b.code),
      ],
    }))
    .filter((g) => g.codes.length > 0)

  // 目录里没有、但档案里有的码（目录未同步时兜底展示，避免「明明有权限却看不到」）
  const known = new Set(catalog.flatMap((g) => [g.menu?.code, ...g.buttons.map((b) => b.code)]).filter(Boolean))
  const orphan = [...mine].filter((c) => !known.has(c))

  return (
    <div style={{ maxWidth: 900 }}>
      {/* antd6 的 Descriptions 没有 loading 属性，加载态用 Skeleton 占位 */}
      {loading ? (
        <Skeleton active paragraph={{ rows: 1 }} title={false} style={{ maxWidth: 420, marginBottom: 8 }} />
      ) : (
        <Descriptions column={2} size="small" style={{ marginBottom: 8 }}>
          <Descriptions.Item label="角色">
            <Space wrap size={4}>
              {rbac?.is_super && <Tag color="red">超级管理员</Tag>}
              {(rbac?.roles || []).map((r) => (
                <Tag key={r.id} color="blue">{r.name}</Tag>
              ))}
              {!rbac?.is_super && !rbac?.roles?.length && <Typography.Text type="secondary">未分配角色</Typography.Text>}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="部门">
            {rbac?.department?.name || <Typography.Text type="secondary">未分配</Typography.Text>}
          </Descriptions.Item>
        </Descriptions>
      )}

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {rbac?.is_super
          ? '超级管理员拥有全部权限，且校验时直接放行。'
          : `共 ${mine.size} 项权限，按模块分组如下。`}
      </Typography.Text>

      <div style={{ marginTop: 16 }}>
        {granted.map((g) => (
          <div key={g.module} style={{ marginBottom: 12 }}>
            <Typography.Text strong style={{ fontSize: 13 }}>{g.name}</Typography.Text>
            <div style={{ marginTop: 6 }}>
              <Space wrap size={4}>
                {g.codes.map((code) => (
                  <Tag key={code} style={{ margin: 0 }}>
                    {nameOf.get(code) || code}
                  </Tag>
                ))}
              </Space>
            </div>
          </div>
        ))}
        {orphan.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Typography.Text strong style={{ fontSize: 13 }}>其它</Typography.Text>
            <div style={{ marginTop: 6 }}>
              <Space wrap size={4}>
                {orphan.map((code) => (
                  <Tag key={code} style={{ margin: 0 }}>{code}</Tag>
                ))}
              </Space>
            </div>
          </div>
        )}
        {!loading && granted.length === 0 && orphan.length === 0 && (
          <Typography.Text type="secondary">当前账号没有任何权限码，请联系管理员分配角色。</Typography.Text>
        )}
      </div>
    </div>
  )
}
