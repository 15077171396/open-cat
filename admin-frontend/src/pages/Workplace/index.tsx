import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Card, Empty, Skeleton, Space, Tag, Typography } from 'antd'
import {
  AuditOutlined,
  BookOutlined,
  CommentOutlined,
  ControlOutlined,
  DeleteOutlined,
  PlusOutlined,
  RightOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import { formatDateTime, formatRelative } from '../../utils/datetime'
import type { DashboardData, TodoGroup, TodoSummary } from '../../types'

interface AuditItem {
  id: number
  admin_account: string
  action_label: string
  target_type_label: string
  summary: string
  risk_level: string
  created_at: string | null
}

interface AuditResponse {
  items: AuditItem[]
}

interface TutorialRow {
  id: number
  title: string
  status: string
  is_published?: boolean
  category: string
  updated_at?: string | null
}

interface TutorialResponse {
  items: TutorialRow[]
  total: number
}

interface QuickAction {
  key: string
  label: string
  description: string
  icon: ReactNode
  path: string
  perms?: string[]
}

const RISK_COLOR: Record<string, string> = {
  danger: 'red',
  money: 'gold',
  normal: 'blue',
}

const STATUS_LABEL: Record<string, string> = {
  updating: '更新中',
  completed: '已完结',
}

function greeting(): string {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 11) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

/**
 * 工作台：概览菜单下的聚合入口。
 *
 * 不新增接口，复用待办 / 看板 / 审计 / 教程列表：
 * - 待办与看板是所有人的入口；
 * - 最近动态和最近教程按权限决定是否请求，没权限就不发请求、显示提示；
 * - 任一接口失败只让对应卡片降级，不把整页拖成白屏。
 */
export default function Workplace() {
  const navigate = useNavigate()
  const { user, isSuper, permissions } = useAuthStore()
  const [todos, setTodos] = useState<TodoSummary | null>(null)
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [audit, setAudit] = useState<AuditItem[] | null>(null)
  const [tutorials, setTutorials] = useState<TutorialRow[] | null>(null)
  const [loading, setLoading] = useState(true)

  const can = (code: string) => isSuper || (permissions?.includes(code) ?? false)
  const canAudit = can('audit:view')
  const canTutorial = can('tutorial:view')
  const canEditTutorial = can('tutorial:edit')
  const canUser = can('user:view')
  const canSetting = can('setting:view')
  const canRecycle =
    can('tutorial:view') || can('comment:view') || can('user:view')

  useEffect(() => {
    let alive = true
    const settle = <T,>(promise: Promise<T>) =>
      promise.then(
        (value) => ({ ok: true as const, value }),
        () => ({ ok: false as const }),
      )

    void (async () => {
      const [todoResult, dashResult, auditResult, tutorialResult] = await Promise.all([
        settle(apiGet<TodoSummary>('/admin/todos')),
        settle(apiGet<DashboardData>('/admin/dashboard')),
        canAudit
          ? settle(apiGet<AuditResponse>('/admin/audit-logs', { params: { page: 1, page_size: 6 } }))
          : Promise.resolve({ ok: false as const }),
        canTutorial
          ? settle(
              apiGet<TutorialResponse>('/admin/tutorials', {
                params: { page: 1, page_size: 5 },
              }),
            )
          : Promise.resolve({ ok: false as const }),
      ])
      if (!alive) return
      setTodos(todoResult.ok ? todoResult.value : null)
      setDashboard(dashResult.ok ? dashResult.value : null)
      setAudit(auditResult.ok ? auditResult.value.items ?? [] : null)
      setTutorials(tutorialResult.ok ? tutorialResult.value.items ?? [] : null)
      setLoading(false)
    })()

    return () => {
      alive = false
    }
  }, [canAudit, canTutorial])

  const displayName = user?.account || '管理员'
  const today = new Date().toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  })

  const todoGroups = useMemo(() => todos?.groups ?? [], [todos])
  const riskCount = todoGroups
    .filter((group) => group.severity === 'error')
    .reduce((sum, group) => sum + group.count, 0)

  const quickActions = useMemo<QuickAction[]>(() => {
    const actions: QuickAction[] = []
    if (can('tutorial:create')) {
      actions.push({
        key: 'new-tutorial',
        label: '新建教程',
        description: '写一篇新内容',
        icon: <PlusOutlined />,
        path: '/tutorials/new',
      })
    }
    if (can('moderation:view') || can('report:view')) {
      actions.push({
        key: 'moderation',
        label: '内容审核',
        description: '处理机审与举报',
        icon: <SafetyCertificateOutlined />,
        path: '/moderation',
      })
    }
    if (canUser) {
      actions.push({
        key: 'users',
        label: '用户管理',
        description: '查用户与分配角色',
        icon: <TeamOutlined />,
        path: '/users',
      })
    }
    if (canRecycle) {
      actions.push({
        key: 'recycle',
        label: '回收站',
        description: '恢复误删内容',
        icon: <DeleteOutlined />,
        path: '/recycle-bin',
      })
    }
    if (can('comment:view')) {
      actions.push({
        key: 'comments',
        label: '评论治理',
        description: '处理评论与回复',
        icon: <CommentOutlined />,
        path: '/comments',
      })
    }
    if (canSetting) {
      actions.push({
        key: 'settings',
        label: '系统设置',
        description: '配置站点与功能',
        icon: <ControlOutlined />,
        path: '/settings',
      })
    }
    return actions.slice(0, 6)
  }, [isSuper, permissions]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="wp-page">
      <section className="wp-hero">
        <div className="wp-hero__main">
          <div className="wp-eyebrow">WORKSPACE</div>
          <Typography.Title level={2} className="wp-title">
            {greeting()}，{displayName}
          </Typography.Title>
          <div className="wp-date">{today}</div>
        </div>
        <div className="wp-hero__stats">
          <div className="wp-stat">
            <span className="wp-stat__label">待我处理</span>
            <strong className="wp-stat__value">{todos?.total ?? '—'}</strong>
          </div>
          <div className="wp-stat">
            <span className="wp-stat__label">今日新增注册</span>
            <strong className="wp-stat__value">{dashboard?.today_registrations ?? '—'}</strong>
          </div>
          <div className="wp-stat">
            <span className="wp-stat__label">累计用户</span>
            <strong className="wp-stat__value">{dashboard?.total_users ?? '—'}</strong>
          </div>
          <div className={`wp-stat${riskCount > 0 ? ' is-alert' : ''}`}>
            <span className="wp-stat__label">失败任务</span>
            <strong className="wp-stat__value">{riskCount || '—'}</strong>
          </div>
        </div>
      </section>

      <div className="wp-grid wp-grid--main">
        <Card
          className="wp-card"
          variant="outlined"
          title="待我处理"
          extra={
            todos && todos.total > 0 ? (
              <Tag color="blue">{todos.total} 项</Tag>
            ) : null
          }
        >
          {loading ? (
            <Skeleton active paragraph={{ rows: 4 }} title={false} />
          ) : todoGroups.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待办，保持这个状态" />
          ) : (
            <div className="wp-todo-list">
              {todoGroups.map((group: TodoGroup) => (
                <button
                  key={group.key}
                  type="button"
                  className="wp-todo"
                  onClick={() => navigate(group.path)}
                >
                  <span className={`wp-todo__dot wp-todo__dot--${group.severity}`} aria-hidden="true" />
                  <span className="wp-todo__body">
                    <span className="wp-todo__title">{group.label}</span>
                    {group.detail ? <span className="wp-todo__detail">{group.detail}</span> : null}
                  </span>
                  <span className="wp-todo__count">{group.count}</span>
                  <RightOutlined className="wp-todo__arrow" aria-hidden="true" />
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card className="wp-card" variant="outlined" title="快捷入口">
          {quickActions.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前角色没有可用快捷入口" />
          ) : (
            <div className="wp-actions">
              {quickActions.map((action) => (
                <button
                  key={action.key}
                  type="button"
                  className="wp-action"
                  onClick={() => navigate(action.path)}
                >
                  <span className="wp-action__icon">{action.icon}</span>
                  <span className="wp-action__body">
                    <span className="wp-action__label">{action.label}</span>
                    <span className="wp-action__desc">{action.description}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="wp-grid wp-grid--two">
        <Card className="wp-card" variant="outlined" title="最近动态" extra={<span className="wp-muted">最近 6 条</span>}>
          {!canAudit ? (
            <div className="wp-muted">需要「操作审计」查看权限</div>
          ) : audit === null ? (
            <Skeleton active paragraph={{ rows: 4 }} title={false} />
          ) : audit.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无操作记录" />
          ) : (
            <div className="wp-audit-list">
              {audit.map((item) => (
                <div className="wp-audit" key={item.id}>
                  <AuditOutlined className="wp-audit__icon" />
                  <div className="wp-audit__body">
                    <div className="wp-audit__title">
                      {item.action_label}
                      {item.target_type_label ? (
                        <Tag color={RISK_COLOR[item.risk_level] || 'default'} bordered={false}>
                          {item.target_type_label}
                        </Tag>
                      ) : null}
                    </div>
                    <div className="wp-audit__meta">
                      {item.admin_account} · {formatRelative(item.created_at)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card
          className="wp-card"
          variant="outlined"
          title="最近教程"
          extra={
            canTutorial ? (
              <Button type="link" size="small" onClick={() => navigate('/tutorials')}>
                全部教程
              </Button>
            ) : null
          }
        >
          {!canTutorial ? (
            <div className="wp-muted">需要「教程管理」查看权限</div>
          ) : tutorials === null ? (
            <Skeleton active paragraph={{ rows: 4 }} title={false} />
          ) : tutorials.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有教程" />
          ) : (
            <div className="wp-tutorial-list">
              {tutorials.map((tutorial) => (
                <button
                  key={tutorial.id}
                  type="button"
                  className="wp-tutorial"
                  onClick={() =>
                    navigate(
                      canEditTutorial
                        ? `/tutorials/${tutorial.id}/edit`
                        : `/tutorials?keyword=${encodeURIComponent(tutorial.title)}`,
                    )
                  }
                >
                  <BookOutlined className="wp-tutorial__icon" />
                  <span className="wp-tutorial__body">
                    <span className="wp-tutorial__title">{tutorial.title}</span>
                    <span className="wp-tutorial__meta">
                      {tutorial.category || '未分类'} ·{' '}
                      {tutorial.is_published ? '已发布' : '未发布'} ·{' '}
                      {STATUS_LABEL[tutorial.status] || tutorial.status}
                      {tutorial.updated_at ? ` · ${formatDateTime(tutorial.updated_at)}` : ''}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="wp-foot">
        <Space size={6}>
          <UserOutlined />
          <span>
            {user?.account || '—'} · {isSuper ? '超级管理员' : '管理员'}
          </span>
        </Space>
      </div>
    </div>
  )
}
