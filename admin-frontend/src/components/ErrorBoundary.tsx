import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

/**
 * 全局错误边界：捕获任意子树渲染期异常避免整站白屏。
 * 刻意不依赖 antd，保证 antd 未加载时也能展示兜底 UI；懒加载 chunk 404 的错误同样会被捕获。
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 保留完整堆栈便于排查；后续可接入上报服务
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="error-boundary">
          <h2 className="error-boundary-title">页面发生异常</h2>
          <p className="error-boundary-desc">
            页面渲染出现未知错误，请刷新重试；若反复出现，请将控制台日志反馈给开发人员。
          </p>
          <button className="error-boundary-retry" type="button" onClick={() => window.location.reload()}>
            刷新页面
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
