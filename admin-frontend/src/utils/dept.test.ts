import { describe, expect, it } from 'vitest'

import { buildDeptTree } from './dept'
import type { Department } from '../types'

const dept = (id: number, name: string, parent_id: number | null = null): Department => ({
  id,
  name,
  parent_id,
  sort_order: 0,
  created_at: null,
})

describe('buildDeptTree', () => {
  it('平铺列表 → 树：子节点挂到父节点下', () => {
    const tree = buildDeptTree([dept(1, '总部'), dept(2, '技术部', 1), dept(3, '前端组', 2)])
    expect(tree).toHaveLength(1)
    expect(tree[0].title).toBe('总部')
    expect(tree[0].children[0].title).toBe('技术部')
    expect(tree[0].children[0].children[0].title).toBe('前端组')
    expect(tree[0].children[0].children[0].children).toEqual([])
  })

  it('多个根节点保持输入顺序（树里顺序不能乱，否则选择器看起来是随机的）', () => {
    const tree = buildDeptTree([dept(1, 'A'), dept(2, 'B'), dept(3, 'C')])
    expect(tree.map((n) => n.title)).toEqual(['A', 'B', 'C'])
  })

  it('父部门不在列表里 → 当根节点，不能丢', () => {
    // 只加载了子树（父已删或分页只给了子部门）时，节点必须仍然可见
    const tree = buildDeptTree([dept(5, '孤儿部门', 999)])
    expect(tree).toHaveLength(1)
    expect(tree[0].title).toBe('孤儿部门')
  })

  it('parent_id 指向自己 → 当根节点，不能形成环', () => {
    const tree = buildDeptTree([dept(7, '自引用', 7)])
    expect(tree).toHaveLength(1)
    expect(tree[0].children).toEqual([])
  })

  it('空输入返回空数组（调用方可能还没加载完）', () => {
    expect(buildDeptTree([])).toEqual([])
  })

  it('每个节点只出现一次（父指针重复不会导致重复挂载）', () => {
    const tree = buildDeptTree([dept(1, '总部'), dept(2, '技术部', 1), dept(3, '市场部', 1)])
    const count = (nodes: ReturnType<typeof buildDeptTree>): number =>
      nodes.reduce((sum, n) => sum + 1 + count(n.children), 0)
    expect(count(tree)).toBe(3)
  })
})
