import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TableEmpty } from './TableEmpty'

/**
 * 后台列表空态的守卫（MNT-09）。
 *
 * 这个组件是从 15 个 List 页的逐字重复里抽出来的。抽错了不会报错 ——
 * 只会表现为「筛选无结果时提示『还没有数据』」或者「新建按钮在筛选态也出现」。
 * 所以这里把三种分支都钉住。
 */
describe('TableEmpty', () => {
  it('筛选无结果：只给筛选文案与清空按钮，不出现新建入口', async () => {
    const onReset = vi.fn()
    const onCreate = vi.fn()
    render(
      <TableEmpty
        hasFilter
        filteredText="没有符合筛选条件的节点"
        emptyText="还没有节点"
        onReset={onReset}
        create={{ label: '新建节点', onClick: onCreate }}
      />,
    )

    expect(screen.getByText('没有符合筛选条件的节点')).toBeInTheDocument()
    expect(screen.queryByText('还没有节点')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新建节点' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '清空筛选条件' }))
    expect(onReset).toHaveBeenCalledTimes(1)
    expect(onCreate).not.toHaveBeenCalled()
  })

  it('空库：给空态文案与新建按钮，不出现清空筛选', async () => {
    const onCreate = vi.fn()
    render(
      <TableEmpty
        hasFilter={false}
        filteredText="没有符合筛选条件的节点"
        emptyText="还没有节点"
        onReset={vi.fn()}
        create={{ label: '新建节点', onClick: onCreate }}
      />,
    )

    expect(screen.getByText('还没有节点')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '清空筛选条件' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '新建节点' }))
    expect(onCreate).toHaveBeenCalledTimes(1)
  })

  it('没有新建入口时只渲染文案', () => {
    render(<TableEmpty hasFilter={false} filteredText="x" emptyText="暂无数据" />)
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
