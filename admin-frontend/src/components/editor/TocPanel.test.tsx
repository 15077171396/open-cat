/** TocPanel 组件测试：钉住渲染/缩进/空态/点击回调，并证明从 QuillEditor 抽出未改变行为 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import TocPanel, { type TocItem } from './TocPanel'

const ITEMS: TocItem[] = [
  { id: 'h-1', text: '第一章', level: 1 },
  { id: 'h-2', text: '第一节', level: 2 },
  { id: 'h-3', text: '第二节', level: 2 },
]

describe('TocPanel', () => {
  it('渲染标题与全部目录项', () => {
    render(<TocPanel items={ITEMS} onSelect={() => {}} />)
    expect(screen.getByText('目录')).toBeInTheDocument()
    for (const item of ITEMS) {
      expect(screen.getByText(item.text)).toBeInTheDocument()
    }
  })

  it('空列表时展示引导文案，且不渲染任何目录项', () => {
    render(<TocPanel items={[]} onSelect={() => {}} />)
    expect(screen.getByText(/添加标题后/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('点击目录项回调对应 id（不是索引、不是文案）', async () => {
    const onSelect = vi.fn()
    render(<TocPanel items={ITEMS} onSelect={onSelect} />)

    await userEvent.click(screen.getByText('第一节'))

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('h-2')
  })

  it('缩进按层级计算：level 1 无缩进，每深一级 +12px', () => {
    render(<TocPanel items={ITEMS} onSelect={() => {}} />)
    expect(screen.getByText('第一章')).toHaveStyle({ paddingLeft: '0px' })
    expect(screen.getByText('第一节')).toHaveStyle({ paddingLeft: '12px' })
  })

  it('每个目录项都是 button（可键盘聚焦，不是裸 div）', () => {
    render(<TocPanel items={ITEMS} onSelect={() => {}} />)
    expect(screen.getAllByRole('button')).toHaveLength(ITEMS.length)
  })

  it('列表变化后重新渲染，不残留旧项', () => {
    const { rerender } = render(<TocPanel items={ITEMS} onSelect={() => {}} />)
    rerender(<TocPanel items={[ITEMS[0]]} onSelect={() => {}} />)
    expect(screen.getByText('第一章')).toBeInTheDocument()
    expect(screen.queryByText('第一节')).not.toBeInTheDocument()
  })
})
