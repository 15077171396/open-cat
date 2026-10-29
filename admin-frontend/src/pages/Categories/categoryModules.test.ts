/**
 * 分类页「内容类型分组」的来源守卫。
 *
 * 回归背景：资源 / 知识百科 / 扩展 / 在线工具下线后，分类页仍渲染五个**写死**的 Tab，
 * 点进已下线的类型去新增分类会直接 422 —— 前端不报错，只是多了四个死入口。
 * 这类「前端硬编码了后端的枚举」只能靠读源码钉住：组件渲染测试覆盖不到「将来又加一个 Tab」。
 */

import { describe, expect, it } from 'vitest'
import source from './List.tsx?raw'

describe('分类页的内容类型分组', () => {
  it('模块清单不得在前端写死', () => {
    for (const gone of ["'resource'", "'node'", "'plugin'", "'tool'"]) {
      expect(source, `List.tsx 里又出现了硬编码模块 ${gone}`).not.toContain(gone)
    }
  })

  it('模块清单从后端取（派生自内容类型注册表）', () => {
    expect(source).toContain("apiGet<{ modules: CategoryModule[] }>('/categories')")
  })

  it('只有一种内容类型时不渲染 Tab（单 Tab 是纯噪音）', () => {
    expect(source).toContain('modules.length > 1')
  })
})
