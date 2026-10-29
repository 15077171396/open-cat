import type { UserRole } from '../../types'

/**
 * 用户角色展示名与配色（列表页 / 详情抽屉共用一份，别在各页面里各写一份）。
 * 单独成文件是为了避免 `List.tsx` 与 `UserDetailDrawer.tsx` 互相 import 形成循环。
 */
export const ROLE_META: Record<UserRole, { text: string; color: string }> = {
  user: { text: '普通用户', color: 'default' },
  vip: { text: 'VIP', color: 'geekblue' },
  admin: { text: '管理员', color: 'red' },
}
