/**
 * 测试环境初始化：注册 jest-dom 断言（toBeInTheDocument 等）。
 *
 * 同时补齐 jsdom 缺失的少量浏览器 API —— QuillEditor 及其依赖（Quill / 拖拽 / 图片裁剪）
 * 在挂载路径上会触碰它们，不补会直接抛错，导致「测不了」而不是「测出问题」。
 */

import '@testing-library/jest-dom/vitest'

// jsdom 未实现 matchMedia（antd 的响应式栅格、主题检测会用到）
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

// jsdom 未实现带伪元素的 getComputedStyle：antd Modal 量滚动条宽度时会传 pseudoElt，
// 不补会向 stderr 打 "Not implemented" 噪音（不影响断言，但会淹没真正的失败）。
const nativeGetComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => nativeGetComputedStyle(element)) as typeof window.getComputedStyle

// jsdom 未实现 ResizeObserver（antd 的 Table/Tooltip 等组件依赖）
if (!('ResizeObserver' in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub
}

// jsdom 未实现 IntersectionObserver，也没有布局，无法真正判断可见性。
// observe 时立刻回调一次「已进入视口」：这样依赖可见性的懒加载/计时逻辑
// 在测试里等价于「元素一挂载就可见」，而不是永远等不到回调。
if (!('IntersectionObserver' in globalThis)) {
  class IntersectionObserverStub {
    private readonly callback: IntersectionObserverCallback

    constructor(callback: IntersectionObserverCallback) {
      this.callback = callback
    }

    observe(target: Element) {
      this.callback(
        [{ isIntersecting: true, target } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver,
      )
    }

    unobserve() {}

    disconnect() {}

    takeRecords() {
      return []
    }
  }
  ;(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver =
    IntersectionObserverStub
}

// jsdom 的 getBoundingClientRect 恒返回 0，某些组件会据此判断「不可见」
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}
