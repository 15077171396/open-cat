import type { ReactNode } from 'react'

export interface PageHeaderProps {
  /** 标题前的图标节点（antd icon） */
  icon: ReactNode
  /** 页面标题 */
  title: string
  /** 副标题：一句话说明这个模块是干什么的 */
  subtitle?: ReactNode
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
  return (
    <div className="adm-header">
      <div className="adm-header__main">
        <span className="adm-header__icon">{icon}</span>
        <div style={{ minWidth: 0 }}>
          <div className="adm-header__title">{title}</div>
          {(subtitle || count) && (
            <div className="adm-header__sub">
              {subtitle && <span>{subtitle}</span>}
              {subtitle && count && <span className="adm-sep">·</span>}
              {count && (
                <span>
                  共 <strong>{count.total}</strong> {count.unit ?? '条'}
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
