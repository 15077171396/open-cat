import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Form, Input } from 'antd'
import { DataTable, ListToolbar, ModalForm, PageHeader, SelectionBar, StatusTag, boolTone } from './index'

/**
 * 列表页骨架组件（M2）的守卫。
 *
 * 这些组件是从 15 个列表页逐字重复的结构里抽出来的，抽错不会抛异常，
 * 只会表现为「空表时还挂着分页」「筛选态也能新建」「页头整体被权限包住」这类
 * 只有人肉点页面才发现的退化。所以把**行为契约**钉在这里。
 */
describe('PageHeader', () => {
  it('标题、副标题、总数与操作区都渲染', () => {
    render(
      <PageHeader
        icon={<span>i</span>}
        title="在线工具"
        subtitle="AI 工具按提示词模板调用模型"
        count={{ total: 42, unit: '个' }}
        actions={<button type="button">新建工具</button>}
      />,
    )
    expect(screen.getByText('在线工具')).toBeInTheDocument()
    expect(screen.getByText('AI 工具按提示词模板调用模型')).toBeInTheDocument()
    // 总数与单位在同一个 span 里（「共 42 个」），断言整体文本而不是拆开的文本节点
    expect(screen.getByText(/共/)).toHaveTextContent('共 42 个')
    expect(screen.getByRole('button', { name: '新建工具' })).toBeInTheDocument()
  })

  it('不传 count 时不渲染「共 N 条」', () => {
    render(<PageHeader icon={<span>i</span>} title="积分流水" />)
    expect(screen.queryByText(/共/)).not.toBeInTheDocument()
  })
})

describe('ListToolbar', () => {
  it('有筛选时才出现重置按钮，点击回调一次', async () => {
    const onReset = vi.fn()
    render(
      <ListToolbar hasFilter onReset={onReset}>
        <span>筛选项</span>
      </ListToolbar>,
    )
    await userEvent.click(screen.getByRole('button', { name: /重置/ }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('无筛选时不渲染重置按钮', () => {
    render(
      <ListToolbar hasFilter={false} onReset={vi.fn()}>
        <span>筛选项</span>
      </ListToolbar>,
    )
    expect(screen.queryByRole('button', { name: /重置/ })).not.toBeInTheDocument()
  })
})

describe('DataTable', () => {
  const columns = [{ title: '名称', dataIndex: 'name', key: 'name' }]

  it('有数据时渲染行与分页', () => {
    render(
      <DataTable
        columns={columns}
        dataSource={[{ id: 1, name: '工具甲' }]}
        pagination={{ page: 1, pageSize: 20, total: 1, onPageChange: vi.fn() }}
        empty={{ hasFilter: false, filteredText: '无', emptyText: '暂无' }}
      />,
    )
    expect(screen.getByText('工具甲')).toBeInTheDocument()
    expect(screen.getByText('共 1 条')).toBeInTheDocument()
  })

  it('空数据：展示空态文案且不渲染分页', () => {
    render(
      <DataTable
        columns={columns}
        dataSource={[]}
        pagination={{ page: 1, pageSize: 20, total: 0, onPageChange: vi.fn() }}
        empty={{ hasFilter: false, filteredText: '没有符合条件的工具', emptyText: '还没有工具' }}
      />,
    )
    expect(screen.getByText('还没有工具')).toBeInTheDocument()
    // 0 条还挂「共 0 条」是纯噪音，必须没有
    expect(screen.queryByText(/共.*条/)).not.toBeInTheDocument()
  })

  it('筛选态空数据：给清空筛选而不是新建', async () => {
    const onReset = vi.fn()
    const onCreate = vi.fn()
    render(
      <DataTable
        columns={columns}
        dataSource={[]}
        pagination={{ page: 1, pageSize: 20, total: 0, onPageChange: vi.fn() }}
        empty={{
          hasFilter: true,
          filteredText: '没有符合条件的工具',
          emptyText: '还没有工具',
          onReset,
          create: { label: '新建工具', onClick: onCreate },
        }}
      />,
    )
    expect(screen.getByText('没有符合条件的工具')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新建工具' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '清空筛选条件' }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })
})

describe('StatusTag', () => {
  it('按语义色调渲染，不用 antd 预设色类', () => {
    render(<StatusTag tone="success">已发布</StatusTag>)
    const tag = screen.getByText('已发布')
    expect(tag).toBeInTheDocument()
    expect(tag).toHaveStyle({ color: 'var(--app-success)' })
  })

  it('boolTone：true 走成功色，false 走中性色', () => {
    expect(boolTone(true)).toBe('success')
    expect(boolTone(false)).toBe('neutral')
  })
})

describe('SelectionBar', () => {
  it('未选中时整条不渲染', () => {
    const { container } = render(<SelectionBar count={0} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('选中时显示条数与批量按钮', () => {
    render(
      <SelectionBar count={3}>
        <button type="button">批量删除</button>
      </SelectionBar>,
    )
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '批量删除' })).toBeInTheDocument()
  })
})

describe('ModalForm', () => {
  it('校验不通过时不触发提交', async () => {
    const onSubmit = vi.fn()
    render(
      <ModalForm open title="新建工具" onCancel={vi.fn()} onSubmit={onSubmit}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input />
        </Form.Item>
      </ModalForm>,
    )
    await userEvent.click(screen.getByRole('button', { name: /确\s*定/ }))
    expect(await screen.findByText('请输入名称')).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('校验通过后把表单值交给 onSubmit', async () => {
    const onSubmit = vi.fn()
    render(
      <ModalForm open title="新建工具" onCancel={vi.fn()} onSubmit={onSubmit}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input />
        </Form.Item>
      </ModalForm>,
    )
    await userEvent.type(screen.getByLabelText('名称'), '图片压缩')
    await userEvent.click(screen.getByRole('button', { name: /确\s*定/ }))
    expect(onSubmit).toHaveBeenCalledWith({ name: '图片压缩' })
  })
})
