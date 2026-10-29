/**
 * 列表页骨架的**覆盖率棘轮**。
 *
 * 背景：`components/ui` 里的 PageHeader / ListToolbar / DataTable 是从 15+ 个列表页
 * 逐字重复的结构里抽出来的。抽完如果不锁住，下一次加页面时「复制一段旧页面」就会
 * 把手写外壳带回来 —— 组件库不会报错，只会慢慢退回「每个页面各写一套」。
 *
 * 允许逃生，但必须是有记录的逃生：白名单里每个文件都要写明理由。新增白名单 =
 * 一次显式决策（reviewer 会问「为什么这个页面不能套壳」），而不是悄悄漂移。
 *
 * ⚠️ 用 `import.meta.glob(query: '?raw')` 读源码：本工程没装 @types/node，
 * 用 `node:fs` 会让 `tsc -b` 报 TS2307（见 pageHeaderCoverage.test.ts 的同款说明）。
 */

import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('../../pages/**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** 仍然手写表格外壳的页面（键 = 相对 src/pages 的路径，值 = 理由） */
const RAW_TABLE_ALLOWED: Record<string, string> = {
  'Moderation/List.tsx': '同一页有两个数据集（机审留痕 / 用户举报），举报表在 Tabs 内，套 Card 会套进 Card',
  'SearchKeywords/index.tsx': '关键词报告是「指标卡 + 多个小表」的组合页，表格不是页面主体',
  'Readiness/index.tsx': '体检清单是卡片列表，不是表格',
}

/** 仍然手写筛选卡片的页面 */
const RAW_TOOLBAR_ALLOWED: Record<string, string> = {
  'Moderation/List.tsx': '同上：第二个 Tab 的筛选与第一个 Tab 的筛选分开渲染',
  'SearchKeywords/index.tsx': '报告页的筛选是「天数 + 导出」工具条，不是列表筛选',
  'Readiness/index.tsx': '体检页只有刷新按钮，没有筛选条件',
}

function pageFiles(): string[] {
  return Object.keys(SOURCES)
    .filter((p) => p.startsWith('../../pages/'))
    .map((p) => p.replace('../../pages/', ''))
}

describe('列表页骨架覆盖率', () => {
  it('扫描范围本身要有内容（防止 glob 失效导致空断言）', () => {
    expect(pageFiles().length).toBeGreaterThan(20)
  })

  it('手写表格外壳的页面只剩白名单里的', () => {
    const offenders = pageFiles().filter(
      (f) => SOURCES[`../../pages/${f}`]?.includes('className="adm-tablecard"') && !(f in RAW_TABLE_ALLOWED),
    )
    expect(offenders, `这些页面应改用 <DataTable>：${offenders.join(', ')}`).toEqual([])
  })

  it('手写筛选卡片的页面只剩白名单里的', () => {
    const offenders = pageFiles().filter(
      (f) => SOURCES[`../../pages/${f}`]?.includes('className="adm-toolbar"') && !(f in RAW_TOOLBAR_ALLOWED),
    )
    expect(offenders, `这些页面应改用 <ListToolbar>：${offenders.join(', ')}`).toEqual([])
  })

  it('白名单里的文件必须仍然存在（删了页面要同步删条目）', () => {
    for (const f of [...Object.keys(RAW_TABLE_ALLOWED), ...Object.keys(RAW_TOOLBAR_ALLOWED)]) {
      expect(pageFiles(), `白名单条目 ${f} 已不存在`).toContain(f)
    }
  })
})
