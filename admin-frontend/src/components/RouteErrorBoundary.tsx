import { Component } from 'react'
import type { ReactNode } from 'react'
import { Button, Result, Typography } from 'antd'

interface RouteErrorBoundaryProps {
  children: ReactNode
  /** 路由路径：变化时自动清掉错误（换页即重试），同时显示在卡片上便于定位 */
  path: string
}

interface RouteErrorBoundaryState {
  error: Error | null
}

/**
 * **页面级**错误边界：只包住布局的内容区（`<Content>` 里的 `<Outlet />`）。
 *
 * 为什么需要它（2026-10-27）：顶层 `ErrorBoundary` 包在 `BrowserRouter` 外面，
 * 任意一个页面渲染抛错会把**整个后台**替换成兜底页 —— 侧栏、顶栏、面包屑全没了，
 * 用户只能看到「页面发生异常」，连自己在哪一页都判断不了，只能重开整个应用。
 * 包在内容区之后：侧栏还在（可以点去别的页面），卡片上直接写着**是哪一页**挂的、
 * 报的什么错，并且「重试」只重挂这一页（换路由也会自动复位）。
 *
 * 刻意不吞 `console.error`：错误详情既要在控制台可查，也要能让人截图反馈。
 */
export default class RouteErrorBoundary extends Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error(`[RouteErrorBoundary] ${this.props.path}`, error, info.componentStack)
  }

  componentDidUpdate(prev: RouteErrorBoundaryProps) {
    // 换页自动复位：否则从坏页面点到好页面仍然显示上一页的错误
    if (prev.path !== this.props.path && this.state.error) {
      this.setState({ error: null })
    }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <Result
        status="warning"
        title="此页面渲染失败（其它页面仍可正常使用）"
        subTitle={
          <>
            <div>
              路径：<Typography.Text code>{this.props.path}</Typography.Text>
            </div>
            <Typography.Paragraph
              type="secondary"
              style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}
            >
              {error.name}: {error.message}
            </Typography.Paragraph>
          </>
        }
        extra={
          <Button type="primary" onClick={() => this.setState({ error: null })}>
            重试这一页
          </Button>
        }
      />
    )
  }
}
