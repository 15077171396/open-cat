import { describe, expect, it } from 'vitest'

import {
  externalLeafKeys,
  filterMenuTree,
  groupMenuKey,
  nextOpenKeys,
  resolveOpenGroupKey,
  resolveSelectedKey,
} from './menu'
import type { MenuTree } from '../types'

/**
 * 侧边菜单纯逻辑测试。
 *
 * 这些边界在组件里只能靠 E2E 发现（而且错了也不报错，只是「菜单少一项 / 高亮错页」），
 * 所以把规则钉在单测里：
 * - 权限三态（perm / perms / 都不带）与 `permissions === null`（档案未加载）
 * - 空分组必须整组剔除
 * - `/` 只做全等匹配，其余按前缀 + 最长优先
 */

const TREE: MenuTree = {
  groups: [
    {
      key: 'overview',
      label: '概览',
      icon: 'home',
      items: [
        { key: '/', label: '数据看板', icon: 'dashboard', perm: 'dashboard:view' },
        {
          key: 'umami-stats',
          label: '访问统计',
          icon: 'chart',
          perm: 'dashboard:view',
          url: 'https://tj.example.com/',
        },
      ],
    },
    {
      key: 'content',
      label: '内容管理',
      icon: 'file-text',
      items: [
        { key: '/tutorials', label: '教程管理', icon: 'book', perm: 'tutorial:view' },
        { key: '/nodes', label: '知识百科', icon: 'appstore', perm: 'node:view' },
      ],
    },
    {
      key: 'operations',
      label: '运营管理',
      icon: 'notification',
      items: [
        {
          key: '/moderation',
          label: '内容审核',
          icon: 'audit',
          perms: ['moderation:view', 'report:view'],
        },
      ],
    },
  ],
  footer: [{ key: '/profile', label: '个人中心', icon: 'user' }],
}

const leafKeys = (tree: MenuTree) => tree.groups.flatMap((g) => g.items.map((i) => i.key))
const groupKeys = (tree: MenuTree) => tree.groups.map((g) => g.key)

describe('filterMenuTree', () => {
  it('super_admin 全量可见', () => {
    const visible = filterMenuTree(TREE, [], true)
    expect(groupKeys(visible)).toEqual(['overview', 'content', 'operations'])
    expect(visible.footer).toHaveLength(1)
  })

  it('permissions 为 null（RBAC 档案未加载）时只保留不带权限码的项', () => {
    const visible = filterMenuTree(TREE, null, false)
    // 所有分组内的项都带权限码 → 分组全部消失，只剩个人中心
    expect(visible.groups).toEqual([])
    expect(visible.footer.map((i) => i.key)).toEqual(['/profile'])
  })

  it('按 perm 单码过滤，子项全被裁掉的分组整组不渲染', () => {
    const visible = filterMenuTree(TREE, ['tutorial:view'], false)
    expect(groupKeys(visible)).toEqual(['content'])
    expect(leafKeys(visible)).toEqual(['/tutorials'])
    expect(visible.footer.map((i) => i.key)).toEqual(['/profile'])
  })

  it('perms 任一命中即显示（内容审核页有两个查看权限）', () => {
    expect(leafKeys(filterMenuTree(TREE, ['report:view'], false))).toEqual(['/moderation'])
    expect(leafKeys(filterMenuTree(TREE, ['moderation:view'], false))).toEqual(['/moderation'])
  })

  it('不带任何权限码的项对所有人可见', () => {
    expect(filterMenuTree(TREE, [], false).footer.map((i) => i.key)).toEqual(['/profile'])
  })
})

describe('resolveSelectedKey', () => {
  const visible = filterMenuTree(TREE, [], true)

  it('精确命中', () => {
    expect(resolveSelectedKey(visible, '/nodes')).toBe('/nodes')
  })

  it('子路径命中父菜单（/tutorials/12/edit → /tutorials）', () => {
    expect(resolveSelectedKey(visible, '/tutorials/12/edit')).toBe('/tutorials')
  })

  it('根路径只做全等匹配：未知路径不会把首页点亮', () => {
    expect(resolveSelectedKey(visible, '/')).toBe('/')
    expect(resolveSelectedKey(visible, '/unknown-page')).toBe('/')
  })

  it('更长的 key 优先命中', () => {
    const tree: MenuTree = {
      groups: [
        {
          key: 'g',
          label: 'G',
          icon: 'home',
          items: [
            { key: '/points', label: '积分流水', icon: 'wallet' },
            { key: '/points/detail', label: '积分明细', icon: 'wallet' },
          ],
        },
      ],
      footer: [],
    }
    expect(resolveSelectedKey(tree, '/points/detail/9')).toBe('/points/detail')
  })
})

describe('resolveOpenGroupKey', () => {
  const visible = filterMenuTree(TREE, [], true)

  it('返回选中项所在分组的 antd key', () => {
    expect(resolveOpenGroupKey(visible, '/nodes')).toBe(groupMenuKey('content'))
    expect(groupMenuKey('content')).toBe('group:content')
  })

  it('选中项在 footer（个人中心）时不展开任何分组', () => {
    expect(resolveOpenGroupKey(visible, '/profile')).toBeNull()
  })
})

describe('externalLeafKeys', () => {
  it('只收集带 url 的项（点击时不能走 SPA 路由）', () => {
    const keys = externalLeafKeys(filterMenuTree(TREE, [], true))
    expect([...keys]).toEqual(['umami-stats'])
  })
})

describe('nextOpenKeys（手风琴：同时只展开一个分组）', () => {
  const A = 'group:ops'
  const B = 'group:system'

  it('展开第二个分组时，第一个自动收起', () => {
    // antd 传的是「操作后的完整集合」，这里是 [A, B]（用户点了 B）
    expect(nextOpenKeys([A], [A, B])).toEqual([B])
  })

  it('从空开始展开第一个', () => {
    expect(nextOpenKeys([], [A])).toEqual([A])
  })

  it('收起当前分组后一个都不留', () => {
    expect(nextOpenKeys([A], [])).toEqual([])
  })

  it('无变化时不会误收起（antd 可能重复上报同一集合）', () => {
    expect(nextOpenKeys([A], [A])).toEqual([A])
  })

  it('历史遗留的多开状态会被收敛成一个（只留最后那个）', () => {
    expect(nextOpenKeys([A, B], [A])).toEqual([A])
    expect(nextOpenKeys([A, B], [A, B])).toEqual([B])
  })

  it('空输入安全', () => {
    expect(nextOpenKeys([], [])).toEqual([])
  })
})
