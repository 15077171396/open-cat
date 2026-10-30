import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge, Button, Empty, Popover, Typography } from 'antd'
import { BellOutlined, ReloadOutlined, RightOutlined } from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { getApiErrorStatus } from '../../utils/error'
import { hasMessage, useI18n } from '../../i18n'
import type { TodoGroup, TodoSummary } from '../../types'

/**
 * 顶栏待办铃铛：把「待审实名 / 待处理举报 / 待复核机审 / 待回反馈 / 失败任务 / AI 额度」
 * 收口成一个入口。
 *
 * 计数与权限过滤全在后端（`GET /admin/todos`），这里只负责呈现与跳转：
 * - **不复制计数口径**：各业务域的「待处理」定义会变，前端再算一遍必然漂移；
 * - **不做二次权限判断**：后端只下发当前管理员能打开的类别，前端判权会变成第二份权限表；
 * - 旧后端没有该接口（404）时整个铃铛隐藏，不要让一次升级顺序把顶栏变成错误提示。
 */

/** 轮询间隔：待办不是实时数据，60 秒足够；窗口重新聚焦时另外刷新一次（节流 30 秒） */
const POLL_MS = 60_000
const FOCUS_THROTTLE_MS = 30_000
/** 路由切换后的刷新节流：刚处理完一条返回列表时立即更新，但不让快速翻页打出一串请求 */
const NAV_THROTTLE_MS = 10_000

function normalizeSummary(data: TodoSummary | null | undefined): TodoSummary {
  const groups = (data?.groups ?? []).filter(
    (group): group is TodoGroup =>
      !!group && typeof group.key === 'string' && Number(group.count) > 0,
  )
  return {
    total: groups.reduce((sum, group) => sum + Number(group.count), 0),
    groups,
  }
}

export default function TodoBell() {
  const navigate = useNavigate()
  const location = useLocation()
  const { locale, t } = useI18n()
  const [summary, setSummary] = useState<TodoSummary>({ total: 0, groups: [] })
  const [open, setOpen] = useState(false)
  const [failed, setFailed] = useState(false)
  const [hidden, setHidden] = useState(false)
  const [loading, setLoading] = useState(false)
  const lastLoadAt = useRef(0)

  const load = useCallback(async (force = false) => {
    if (!force && Date.now() - lastLoadAt.current < NAV_THROTTLE_MS) return
    lastLoadAt.current = Date.now()
    setLoading(true)
    try {
      const data = await apiGet<TodoSummary>('/admin/todos')
      setSummary(normalizeSummary(data))
      setFailed(false)
      setHidden(false)
    } catch (error) {
      // 旧后端（没有 /admin/todos）时直接隐藏铃铛；升级期间顶栏不该出现一个坏掉的入口
      if (getApiErrorStatus(error) === 404) {
        setHidden(true)
        return
      }
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(true)
  }, [load])

  // 路由切换后刷新：处理完待办返回列表时，红点应当立刻变化，而不是最多等一分钟
  useEffect(() => {
    void load()
  }, [load, location.pathname, location.search])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load(true)
    }, POLL_MS)
    let lastFocusAt = 0
    const onFocus = () => {
      const now = Date.now()
      if (now - lastFocusAt < FOCUS_THROTTLE_MS) return
      lastFocusAt = now
      if (document.visibilityState === 'visible') void load(true)
    }
    window.addEventListener('focus', onFocus)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [load])

  if (hidden) return null

  const openGroup = (group: TodoGroup) => {
    setOpen(false)
    navigate(group.path)
  }

  /**
   * 类别名优先用本地词表（按 `group.key`），没有译文才回退接口下发的中文。
   * `detail` 是后端拼的业务明细（任务名/剩余量），属于数据不是文案，保持原样。
   */
  const groupLabel = (group: TodoGroup) => {
    const key = `todo.group.${group.key}`
    return hasMessage(locale, key) ? t(key) : group.label
  }

  const content = (
    <div className="todo-pop">
      <div className="todo-pop__head">
        <Typography.Text strong>{t('todo.title')}</Typography.Text>
        <Button
          type="text"
          size="small"
          icon={<ReloadOutlined />}
          loading={loading}
          onClick={() => void load(true)}
          aria-label={t('todo.refresh')}
        />
      </div>
      {failed && <div className="todo-pop__error">{t('todo.loadFailed')}</div>}
      {summary.groups.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t('todo.empty')}
          style={{ margin: '16px 0' }}
        />
      ) : (
        <div className="todo-pop__list">
          {summary.groups.map((group) => {
            const label = groupLabel(group)
            return (
              <button
                key={group.key}
                type="button"
                className="todo-item"
                onClick={() => openGroup(group)}
                aria-label={t('todo.itemAria', { label, count: group.count })}
              >
                <span
                  className={`todo-item__dot todo-item__dot--${group.severity}`}
                  aria-hidden="true"
                />
                <span className="todo-item__body">
                  <span className="todo-item__title">{label}</span>
                  {group.detail && <span className="todo-item__detail">{group.detail}</span>}
                </span>
                <span className="todo-item__count">
                  {group.count > 99 ? '99+' : group.count}
                </span>
                <RightOutlined className="todo-item__arrow" aria-hidden="true" />
              </button>
            )
          })}
        </div>
      )}
    </div>
  )

  return (
    <Popover
      content={content}
      trigger="click"
      placement="bottomRight"
      open={open}
      onOpenChange={setOpen}
      arrow={false}
    >
      <Badge count={summary.total} size="small" overflowCount={99} offset={[-4, 4]}>
        <Button
          type="text"
          icon={<BellOutlined />}
          aria-label={
            summary.total > 0
              ? t('todo.badgeWithCount', { count: summary.total })
              : t('todo.badge')
          }
          title={t('todo.badge')}
        />
      </Badge>
    </Popover>
  )
}
