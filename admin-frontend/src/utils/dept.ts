import type { Department } from '../types'

/** 部门树节点（antd TreeSelect / Tree 的通用形状） */
export interface DeptTreeNode {
  value: number
  title: string
  children: DeptTreeNode[]
}

/**
 * 部门平铺列表 → 树。
 *
 * 抽出来的原因：`CreateUserModal` 与 `UserRbacModal` 各写过一份**完全相同**的实现，
 * 两处都用 `any[]`（eslint 警告），而且内部是 `byId.forEach` 里再 `departments.find`，
 * 部门一多就是 O(n²)。这里改成两趟 O(n)：先建索引，再挂父子。
 *
 * 两种边界情况刻意处理成「宁可挂到根上，也不丢节点、不递归」：
 * - `parent_id` 指向不存在的部门（父被删了 / 只加载了子树）→ 当作根，节点仍然可见；
 * - `parent_id` 指向自己（脏数据）→ 同样当根，避免把自己塞进自己的 children 造成环。
 */
export function buildDeptTree(departments: Department[]): DeptTreeNode[] {
  const byId = new Map<number, DeptTreeNode>()
  for (const d of departments) {
    byId.set(d.id, { value: d.id, title: d.name, children: [] })
  }

  const roots: DeptTreeNode[] = []
  for (const d of departments) {
    const node = byId.get(d.id)
    if (!node) continue
    const parent = d.parent_id != null ? byId.get(d.parent_id) : undefined
    if (parent && parent !== node) parent.children.push(node)
    else roots.push(node)
  }
  return roots
}
