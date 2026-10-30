import { Fragment, useEffect } from 'react'
import type { ReactNode } from 'react'
import { useInRouterContext, useLocation } from 'react-router-dom'
import { useI18n } from '../../i18n'
import { usePageTitleStore } from '../../store/pageTitleStore'

/**
 * 把页头标题注册给标签页。
 *
 * 拆成子组件是因为 `useLocation()` 在 Router 外会直接抛错，
 * 而组件测试/独立页头预览不一定包 Router。用 `useInRouterContext()`
 * 判断后再挂载，既不影响真实后台，也不破坏既有测试。
 */
function RegisterPageTitle({ title }: { title: string }) {
  const location = useLocation()
  const setTitle = usePageTitleStore((s) => s.setTitle)

  useEffect(() => {
    setTitle(location.pathname, title)
  }, [location.pathname, title, setTitle])

  return null
}

export interface PageHeaderProps {
  /** 标题前的图标节点（antd icon） */
  icon: ReactNode
  /** 页面标题 */
  title: string
  /**
   * 副标题：一句话说明这个模块是干什么的。
   *
   * 传数组表示「多段统计」（如 `['维护分类', <>共 <strong>{n}</strong> 个</>]`），
   * 组件负责在段之间插入 `·` 分隔符 —— 否则每个页面都要手写一遍
   * `<span className="adm-sep">·</span>`，还会写错成 `adm-header__dot`
   * （两套类名长得像但只有 `adm-sep` 有样式）。
   */
  subtitle?: ReactNode | ReactNode[]
  /** 右侧操作区（搜索框 / 新建 / 刷新），按「次要操作在前、主操作在后」排 */
  actions?: ReactNode
  /**
   * 记录总数。传了就渲染成副标题末尾的「共 N 条」——
   * 统一放这里，避免每个页面各写一遍 `<span>共 <strong>{total}</strong> 条</span>`。
   */
  count?: { total: number; unit?: string }
}

/**
 * 列表页页头（替代旧的 `ListPageHeader`，后者只支持「图标 + 标题 + 新建」）。
 *
 * 结构对应 `adm-header` 那套 CSS（`styles/sections/adm-common.css`），
 * 这里只是把 15 个列表页逐字重复的 25 行 JSX 收成一处：
 *
 * ```tsx
 * <PageHeader
 *   icon={<ToolOutlined />}
 *   title="在线工具"
 *   subtitle="AI 工具按提示词模板调用模型"
 *   count={{ total, unit: '个' }}
 *   actions={<><Input.Search …/><PermGuard perm="tool:create"><Button …/></PermGuard></>}
 * />
 * ```
 *
 * ⚠️ 页头**不要**整体包在 `PermGuard` 里：那样只有「能新建」的人看得见标题与搜索框。
 * 权限只包住各自的按钮。
 */
export default function PageHeader({ icon, title, subtitle, actions, count }: PageHeaderProps) {
  const { t } = useI18n()
  const inRouter = useInRouterContext()

  // 数组 = 多段统计；过滤掉空值，避免渲染出「· ·」这种连续分隔符
  const parts = (Array.isArray(subtitle) ? subtitle : subtitle ? [subtitle] : []).filter(Boolean)

  return (
    <div className="adm-header">
      {/* 标签页标题来源：菜单名兜不住编辑页/新建页，页头注册的标题才是准的 */}
      {inRouter && <RegisterPageTitle title={title} />}
      <div className="adm-header__main">
        <span className="adm-header__icon">{icon}</span>
        <div style={{ minWidth: 0 }}>
          <div className="adm-header__title">{title}</div>
          {(subtitle || count) && (
            <div className="adm-header__sub">
              {parts.map((part, index) => (
                <Fragment key={index}>
                  {index > 0 && <span className="adm-sep">·</span>}
                  <span>{part}</span>
                </Fragment>
              ))}
              {parts.length > 0 && count && <span className="adm-sep">·</span>}
              {count && (
                <span>
                  {t('common.totalPrefix')}
                  <strong>{count.total}</strong> {count.unit ?? t('common.items')}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
      {actions && <div className="adm-header__actions">{actions}</div>}
    </div>
  )
}
