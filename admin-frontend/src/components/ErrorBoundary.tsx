import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { useI18n } from '../i18n'

interface ErrorBoundaryProps {
  children: ReactNode
}

type Translate = (key: string, vars?: Record<string, string | number>) => string

interface BoundaryProps extends ErrorBoundaryProps {
  t: Translate
}

interface ErrorBoundaryState {
  error: Error | null
}

/**
 * 全局错误边界：捕获任意子树渲染期异常避免整站白屏。
 * 刻意不依赖 antd，保证 antd 未加载时也能展示兜底 UI；懒加载 chunk 404 的错误同样会被捕获。
 */
/** 函数外壳只为拿 `useI18n`；兜底 UI 本身仍不依赖 antd，antd 崩了也能渲染 */
export default function ErrorBoundary(props: ErrorBoundaryProps) {
  const { t } = useI18n()
  return <ErrorBoundaryInner {...props} t={t} />
}

class ErrorBoundaryInner extends Component<BoundaryProps, ErrorBoundaryState> {
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
      const { t } = this.props
      return (
        <div className="error-boundary">
          <h2 className="error-boundary-title">{t('errorBoundary.fatalTitle')}</h2>
          <p className="error-boundary-desc">{t('errorBoundary.fatalDesc')}</p>
          <button className="error-boundary-retry" type="button" onClick={() => window.location.reload()}>
            {t('errorBoundary.reload')}
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
