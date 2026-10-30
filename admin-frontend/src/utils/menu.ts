import type { MenuGroup, MenuLeaf, MenuTree } from '../types'

/**
 * 侧边菜单的纯逻辑：权限过滤 / 扁平化 / 选中与展开定位。
 *
 * 与 `AdminLayout` 拆开的原因：这些规则（尤其是「空分组要剔除」「`/` 不做前缀匹配」）
 * 是容易出错的边界，放在组件里只能靠 E2E 验证，抽成纯函数可以单测。
 *
 * ⚠️ 权限语义必须与后端 `app/services/admin_menu.py` 保持一致：
 * `perm` 单一码 / `perms` 任一命中 / 都不带 = 任何已登录管理员可见。
 */

/** 分组在 antd Menu 里的 key 前缀（避免与叶子路由 key 撞车，如 `/` 与分组 `overview`） */
export const GROUP_KEY_PREFIX = 'group:'

export function groupMenuKey(groupKey: string): string {
  return `${GROUP_KEY_PREFIX}${groupKey}`
}

/** 单个叶子项是否对当前管理员可见 */
function canSeeLeaf(leaf: MenuLeaf, permissions: string[] | null, isSuper: boolean): boolean {
  if (isSuper) return true
  if (leaf.perms && leaf.perms.length > 0) {
    return leaf.perms.some((code) => permissions?.includes(code) ?? false)
  }
  if (leaf.perm) return permissions?.includes(leaf.perm) ?? false
  return true
}

/**
 * 按权限裁剪菜单树。
 *
 * ⚠️ `permissions === null`（RBAC 档案尚未加载）时，除「不带权限码」的项外**全部隐藏** ——
 * 宁可短暂空菜单，也不要先闪出一批无权访问的入口。
 * 子项全被裁掉的分组整组不渲染（否则侧边栏会留一排点开是空的可折叠项）。
 */
export function filterMenuTree(
  tree: MenuTree,
  permissions: string[] | null,
  isSuper: boolean
): MenuTree {
  const groups: MenuGroup[] = []
  for (const group of tree.groups) {
    const items = group.items.filter((leaf) => canSeeLeaf(leaf, permissions, isSuper))
    if (items.length > 0) groups.push({ ...group, items })
  }
  return {
    groups,
    footer: tree.footer.filter((leaf) => canSeeLeaf(leaf, permissions, isSuper)),
  }
}

/** 扁平化全部叶子项（渲染顺序） */
export function flattenLeaves(tree: MenuTree): MenuLeaf[] {
  const leaves: MenuLeaf[] = []
  for (const group of tree.groups) leaves.push(...group.items)
  leaves.push(...tree.footer)
  return leaves
}

/**
 * 当前路由命中的菜单 key。
 *
 * 按 key 长度降序做前缀匹配：`/tutorials/new` 同时以 `/tutorials` 开头，
 * 预排序保证命中更具体的那个；`/` 只做全等匹配（否则任何路径都会命中首页）。
 */
export function resolveSelectedKey(tree: MenuTree, pathname: string): string {
  const keys = flattenLeaves(tree)
    .map((leaf) => leaf.key)
    .sort((a, b) => b.length - a.length)
  return (
    keys.find((key) => pathname === key || (key !== '/' && pathname.startsWith(key))) ?? '/'
  )
}

/** 当前选中项所属的分组 key（antd Menu 的 key）；不在任何分组内返回 null */
export function resolveOpenGroupKey(tree: MenuTree, selectedKey: string): string | null {
  for (const group of tree.groups) {
    if (group.items.some((leaf) => leaf.key === selectedKey)) return groupMenuKey(group.key)
  }
  return null
}

/**
 * 手风琴式展开：左侧菜单**同时只允许一个分组展开**。
 *
 * antd 的 `onOpenChange` 传进来的是「本次操作后的完整展开集合」，不是增量，
 * 所以靠差集判断意图：
 * - 出现新 key = 这次是展开 → 只保留**最新打开**的那个（其余自动收起）；
 * - 没有新 key = 这次是收起 → 剩下的最多留一个。
 *
 * 为什么不让它多开：分组变多以后（运营管理 6 项、系统 8 项），同时展开两三个分组会把
 * 侧栏撑成一长条，底部「个人中心」被挤出视野；而且展开态一多，用户很难看出
 * 「我现在这个页面属于哪一组」——同时只开一个，位置感才成立。
 */
export function nextOpenKeys(current: string[], next: string[]): string[] {
  const opened = next.filter((key) => !current.includes(key))
  if (opened.length > 0) return [opened[opened.length - 1]]
  return next.slice(-1)
}

/** 外链叶子项的 key 集合（点击时不能走 SPA 路由） */
export function externalLeafKeys(tree: MenuTree): Set<string> {
  return new Set(flattenLeaves(tree).filter((leaf) => !!leaf.url).map((leaf) => leaf.key))
}

/** 菜单叶子 key → 当前语言标题（标签页标题的兜底来源） */
export function collectMenuLabels(tree: MenuTree): Map<string, string> {
  const labels = new Map<string, string>()
  for (const leaf of flattenLeaves(tree)) labels.set(leaf.key, leaf.label)
  return labels
}
