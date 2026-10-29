// AiSelectionBubble.test.tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import AiSelectionBubble from './AiSelectionBubble'

function renderBubble(overrides: Partial<Parameters<typeof AiSelectionBubble>[0]> = {}) {
  const onAction = vi.fn()
  const onMore = vi.fn()
  const utils = render(
    <AiSelectionBubble
      visible
      top={0}
      left={0}
      onAction={onAction}
      onMore={onMore}
      {...overrides}
    />,
  )
  return { ...utils, onAction, onMore }
}

describe('AiSelectionBubble', () => {
  it('visible=false 时不渲染内容', () => {
    const { container } = renderBubble({ visible: false })
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('按下快捷操作按钮时阻止默认行为，保住 Quill 选区', () => {
    renderBubble()
    const button = screen.getByRole('button', { name: '改写' })
    // 根节点的 mousedown 处理器 preventDefault 后，fireEvent 返回 false
    const notPrevented = fireEvent.mouseDown(button)
    expect(notPrevented).toBe(false)
  })

  it('点击快捷操作仍会回调对应 operation', () => {
    const { onAction } = renderBubble()
    fireEvent.mouseDown(screen.getByRole('button', { name: '润色' }))
    fireEvent.click(screen.getByRole('button', { name: '润色' }))
    expect(onAction).toHaveBeenCalledTimes(1)
    expect(onAction).toHaveBeenCalledWith('polish')
  })

  it('点击更多会回调 onMore', () => {
    const { onMore } = renderBubble()
    fireEvent.mouseDown(screen.getByRole('button', { name: '更多' }))
    fireEvent.click(screen.getByRole('button', { name: '更多' }))
    expect(onMore).toHaveBeenCalledTimes(1)
  })
})
