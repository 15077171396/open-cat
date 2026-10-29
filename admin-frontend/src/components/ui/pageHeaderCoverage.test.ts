/**
 * 页头骨架覆盖率守卫。
 *
 * 这次重构把 18 个列表页手写的 25 行 `adm-header` 块统一收进 `<PageHeader>`。
 * 但如果不设守卫，下一个新页面很可能又手抄一遍 —— 一年后会同时存在两套页头，
 * 改一次间距要改 19 个地方（这正是重构前 24 个页面手写 `<Table>` 的老问题）。
 *
 * 规则只有一条：`src/pages` 下不允许出现 `className="adm-header"`。
 * 需要定制页头时用 `<PageHeader>` 的 `actions` / `subtitle` 数组，或给组件加属性，
 * 而不是在页面里重新拼一遍 DOM。
 *
 * 用 `import.meta.glob` 而不是 `node:fs`：本工程没装 `@types/node`（前端不需要），
 * 为一条守卫测试引入整个 Node 类型体系不划算；Vite 的 glob 在构建期就把源码读进来了。
 */

import { describe, expect, it } from 'vitest'

const pageSources = import.meta.glob('/src/pages/**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const componentSources = import.meta.glob('/src/components/**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** 文件里是否真的渲染了页头 DOM（注释里提到不算 —— 被改造的页面注释里可能还有这个词） */
function rendersHeader(source: string): boolean {
  return /className="adm-header"/.test(source)
}

describe('页头骨架覆盖率', () => {
  it('pages 下不再手写 adm-header', () => {
    const offenders = Object.entries(pageSources)
      .filter(([path, source]) => !path.includes('.test.') && rendersHeader(source))
      .map(([path]) => path.replace('/src/', 'src/'))

    expect(
      offenders,
      `这些页面仍在手写页头，请改用 <PageHeader>：\n${offenders.join('\n')}`,
    ).toEqual([])
  })

  it('页头 DOM 只在 PageHeader 组件里出现一次', () => {
    const implementations = Object.entries(componentSources)
      .filter(([path, source]) => !path.includes('.test.') && rendersHeader(source))
      .map(([path]) => path.split('/').pop())

    // 出现第二份拷贝就说明有人又造了一个页头组件
    expect(implementations).toEqual(['PageHeader.tsx'])
  })
})
