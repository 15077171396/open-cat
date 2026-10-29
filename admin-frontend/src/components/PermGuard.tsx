import type { ReactNode } from 'react'
import { Result } from 'antd'
import { useAuthStore } from '../store/authStore'

/** 按钮级权限守卫：无权限时不渲染子元素；与 RequirePerm（页面级 403 拦截）互补 */
export function PermGuard({ perm, children }: { perm: string; children: ReactNode }) {
  const isSuper = useAuthStore((s) => s.isSuper)
  const permissions = useAuthStore((s) => s.permissions)
  const allowed = isSuper || (permissions?.includes(perm) ?? false)
  if (!allowed) return null
  return <>{children}</>
}

/** 页面级权限守卫：无权限渲染 403（挂在路由元素外层） */
export function RequirePerm({
  perm,
  anyPerms,
  children,
}: {
  /** 单一权限码 */
  perm?: string
  /** 任一命中即放行（用于「多个权限都能进同一页」的场景，如内容审核页） */
  anyPerms?: string[]
  children: ReactNode
}) {
  const isSuper = useAuthStore((s) => s.isSuper)
  const permissions = useAuthStore((s) => s.permissions)
  const allowed =
    isSuper ||
    (anyPerms?.length
      ? anyPerms.some((code) => permissions?.includes(code) ?? false)
      : (perm ? permissions?.includes(perm) : false) ?? false)
  if (!allowed) {
    return (
      <Result
        status="403"
        title="403"
        subTitle="抱歉，您没有访问该页面的权限，请联系管理员分配角色。"
      />
    )
  }
  return <>{children}</>
}
