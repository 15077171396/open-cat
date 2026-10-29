/**
 * 页面级错误边界的回归：**某一页渲染挂了不能把侧栏一起带走**。
 *
 * 背景（2026-10-27）：顶层 `ErrorBoundary` 包在 `BrowserRouter` 外面，任意页面抛错会把
 * 整个后台换成兜底页 —— 侧栏、顶栏全没了，用户连「是哪一页挂了」都判断不了。
 * 这里钉住三件事：① 冻结在内容区的错误只影响内容区；② 卡片上写清路径与错误消息；
 * ③ `path` 变化（换页）时自动复位。
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import RouteErrorBoundary from './RouteErrorBoundary'

function Boom(): never {
  throw new TypeError('Cannot convert object to primitive value')
}

describe('RouteErrorBoundary', () => {
  it('捕获子组件异常，显示路径与错误消息，并保留外层布局', () => {
    // React 会把错误打到 console.error；这里静音避免污染测试输出
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <div>
        <aside>侧栏还在</aside>
        <RouteErrorBoundary path="/settings">
          <Boom />
        </RouteErrorBoundary>
      </div>,
    )
    expect(screen.getByText('侧栏还在')).toBeTruthy()
    expect(screen.getByText('此页面渲染失败（其它页面仍可正常使用）')).toBeTruthy()
    expect(screen.getByText('/settings')).toBeTruthy()
    expect(screen.getByText(/Cannot convert object to primitive value/)).toBeTruthy()
    spy.mockRestore()
  })

  it('换路径时自动复位（否则从坏页面点到好页面还显示上一页的错误）', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    function Harness() {
      const [path, setPath] = useState('/broken')
      return (
        <div>
          <button type="button" onClick={() => setPath('/ok')}>
            去好页面
          </button>
          <RouteErrorBoundary path={path}>
            {path === '/broken' ? <Boom /> : <span>好页面内容</span>}
          </RouteErrorBoundary>
        </div>
      )
    }
    render(<Harness />)
    expect(screen.getByText('此页面渲染失败（其它页面仍可正常使用）')).toBeTruthy()

    fireEvent.click(screen.getByText('去好页面'))
    expect(screen.getByText('好页面内容')).toBeTruthy()
    spy.mockRestore()
  })

  it('正常子组件原样渲染，不插任何包装', () => {
    render(
      <RouteErrorBoundary path="/settings">
        <span>正常内容</span>
      </RouteErrorBoundary>,
    )
    expect(screen.getByText('正常内容')).toBeTruthy()
  })
})
