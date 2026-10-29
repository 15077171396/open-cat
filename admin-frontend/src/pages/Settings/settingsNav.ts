/**
 * 系统设置的**二级导航分类**（左侧栏）。
 *
 * 为什么不再用横排 tab：抽走 4 个模块后系统设置仍有 14 个分组，横向 tab 挤成一行小字，
 * 找一项设置得先把整行扫一遍，靠位置的「记忆导航」已经失效 —— 分类竖排后每屏最多 6 项，
 * 且能显示「哪一栏里有未保存的修改」。
 *
 * 分类写在**前端**而不是后端，理由是它只影响这一个页面的排版（不影响接口与数据归属），
 * 而后端 `SETTING_GROUPS` 的顺序仍在决定组内顺序。
 * ⚠️ 但「覆盖完整性」由后端测试守着（`tests/test_settings_nav.py` 直接解析这个文件）：
 * 新增一个 settings 分组却没归类时，它会出现在「其它」里而不是消失 —— 与 tab 时代的
 * `GROUP_ORDER.filter(...)` 静默丢组是同一类风险，必须双向锁住。
 */

export interface NavGroup {
  key: string
  label: string
  description: string
}

/** 分类（泛型保留调用方的分组对象，页面还需要它带的 items） */
export interface NavCategory<T extends { key: string } = NavGroup> {
  key: string
  label: string
  groups: T[]
}

/** 分类声明（顺序即左侧栏顺序；分组按此处的顺序展示，而不是后端返回顺序） */
export const NAV_CATEGORIES: { key: string; label: string; groups: string[] }[] = [
  { key: 'basic', label: '基础', groups: ['basic', 'business', 'invite'] },
  { key: 'content', label: '内容与检索', groups: ['moderation', 'search', 'fetch'] },
  {
    key: 'media',
    label: '上传与素材',
    groups: ['upload', 'image_search', 'storage', 'hero', 'default', 'qrcode', 'social'],
  },
  { key: 'ops', label: '安全与站点', groups: ['ratelimit', 'seo'] },
  { key: 'ai', label: 'AI', groups: ['ai_writing'] },
]

/** 未归类的分组落在这一栏（宁可位置难看，也不能让人以为设置项不存在） */
export const FALLBACK_CATEGORY_KEY = 'other'
export const FALLBACK_CATEGORY_LABEL = '其它'

/** 按分类整理后端下发的分组；未归类者追加到「其它」，空分类不渲染 */
export function buildNav<T extends { key: string }>(groups: T[]): NavCategory<T>[] {
  const byKey = new Map(groups.map((g) => [g.key, g]))
  const used = new Set<string>()

  const nav: NavCategory<T>[] = []
  for (const category of NAV_CATEGORIES) {
    const items: T[] = []
    for (const key of category.groups) {
      const group = byKey.get(key)
      // 后端已经没有这个分组（例如整体抽走）：跳过而不是渲染空区块
      if (!group) continue
      items.push(group)
      used.add(key)
    }
    if (items.length) nav.push({ key: category.key, label: category.label, groups: items })
  }

  const rest = groups.filter((g) => !used.has(g.key))
  if (rest.length) {
    nav.push({ key: FALLBACK_CATEGORY_KEY, label: FALLBACK_CATEGORY_LABEL, groups: rest })
  }
  return nav
}

/** 左侧栏里第一个可选项（默认选中） */
export function firstGroupKey<T extends { key: string }>(nav: NavCategory<T>[]): string {
  for (const category of nav) {
    if (category.groups.length) return category.groups[0].key
  }
  return ''
}

/** 当前分类里是否有未保存的修改（左侧栏一级项上的小圆点） */
export function categoryDirty<T extends { key: string }>(
  category: NavCategory<T>,
  dirtyGroups: Set<string>,
): boolean {
  return category.groups.some((g) => dirtyGroups.has(g.key))
}
