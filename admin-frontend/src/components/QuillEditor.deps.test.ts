/**
 * QuillEditor 依赖稳定性静态守卫：modules 依赖若出现随按键变化的量（sourceText/value），
 * react-quill 会销毁重建编辑器（撤销栈清空、粘贴/图片 overlay 失效），jsdom 下测不出来，
 * 故静态断言「必须用 ref 间接调用」——宁可红，不可静默劣化。
 */

import { describe, expect, it } from 'vitest'

// 用 Vite 的 ?raw 读取源码文本（勿改用 node:fs —— 该工程未安装 @types/node，
// `tsc -b` 会因 node:fs 类型缺失直接构建失败，且测试并非构建产物的一部分）。
import SRC from './QuillEditor.tsx?raw'

/** 取 `const NAME = useCallback(` 到其依赖数组结尾的片段 */
function callbackBlock(name: string): string {
  const start = SRC.indexOf(`const ${name} = useCallback(`)
  expect(start, `未找到 ${name}，守卫可能已失效`).toBeGreaterThan(-1)
  const tail = SRC.slice(start)
  const end = tail.indexOf('}, [')
  expect(end, `${name} 的依赖数组未找到`).toBeGreaterThan(-1)
  return tail.slice(0, tail.indexOf(')', end))
}

describe('QuillEditor 依赖稳定性（P1-45①）', () => {
  it('toggleSourceMode 必须用 ref 间接调用 applySource，不得把它放进依赖', () => {
    const block = callbackBlock('toggleSourceMode')
    expect(block).toContain('applySourceRef.current()')
    expect(block).not.toContain('[applySource]')
  })

  it('modules 的依赖项里不得出现随按键变化的量', () => {
    const start = SRC.indexOf('const modules = useMemo(')
    expect(start, '未找到 modules，守卫可能已失效').toBeGreaterThan(-1)
    const depsStart = SRC.indexOf('}), [', start)
    const depsEnd = SRC.indexOf('])', depsStart)
    const deps = SRC.slice(depsStart, depsEnd)
    for (const volatile of ['sourceText', 'value', 'stats', 'previewHtml']) {
      expect(deps, `modules 依赖里出现了 ${volatile} —— 每次按键都会重建编辑器`).not.toContain(
        volatile,
      )
    }
  })

  it('applySource 仍然依赖 sourceText（ref 只是间接层，不能把功能改坏）', () => {
    expect(callbackBlock('applySource')).toContain('[sourceText]')
  })
})
