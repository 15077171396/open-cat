import type { ReactNode } from 'react'

interface AdminSectionProps {
  /** 区块图标 */
  icon: ReactNode
  /** 区块标题 */
  title: string
  /** 标题下方的一行说明（各模块必传：表单区块没有说明时运营容易填错） */
  hint: ReactNode
  children: ReactNode
}

/**
 * 后台编辑页的分区块（资源 / 教程等共用）：图标 + 标题 + 说明 + 内容。
 * 与 `.adm-divider` 搭配使用，替代嵌套 Card —— 页面本身已在 EditPageLayout 的 Card 内，
 * 再套一层 Card 会显得很重。
 */
export default function AdminSection({ icon, title, hint, children }: AdminSectionProps) {
  return (
    <section className="adm-section">
      <div className="adm-section__head">
        <span className="adm-section__icon">{icon}</span>
        <span className="adm-section__title">{title}</span>
      </div>
      <p className="adm-section__hint">{hint}</p>
      {children}
    </section>
  )
}
