/**
 * 「切章必须先存住当前正文」的回归测试：给章节树 memo 化重构装安全网。
 * 点击回调改为读 ref 后若做错，症状是键入后切章正文被旧值覆盖（静默、不可撤销）。
 * 本测试在当前实现与重构后都必须绿。QuillEditor 换成 textarea：jsdom 下 Quill 内核不可信，
 * 这里只验页面自己的数据流。
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const put = vi.fn(async (..._args: [string, unknown]) => ({ data: {} }))
const apiGet = vi.fn(async (..._args: [string]) => ({}))

vi.mock('../../api/client', () => ({
  default: {
    put: (url: string, body: unknown) => put(url, body),
    post: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
  apiGet: (url: string) => apiGet(url),
  apiPost: vi.fn(async () => ({})),
}))

// 富文本编辑器换成受控 textarea：让「键入」是真实 DOM 事件，走真实 onChange → setState。
// 页面上有**两个**编辑器（课程介绍 + 章节正文），用 placeholder 当可访问名区分。
vi.mock('../../components/QuillEditor', () => ({
  default: ({
    value,
    onChange,
    placeholder,
  }: {
    value?: string
    onChange?: (v: string) => void
    placeholder?: string
  }) => (
    <textarea
      aria-label={placeholder || 'editor'}
      value={value ?? ''}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}))

vi.mock('../../components/EditPageLayout', () => ({
  default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('../../components/NetdiskLinksEditor', () => ({ default: () => null }))
vi.mock('../../hooks/useCategoryOptions', () => ({ useCategoryOptions: () => [] }))

import EditTutorial from './Edit'

// 章节正文编辑器的可访问名（与 Edit.tsx 里 QuillEditor 的 placeholder 一致）
const NODE_EDITOR = '请输入本章节内容...（章内使用标题可生成右侧大纲）'
const NODE_A = { id: 11, title: '第一章 环境搭建', content: '<p>A 的原始正文</p>', parent_id: null }
const NODE_B = { id: 22, title: '第二章 第一个流程', content: '<p>B 的原始正文</p>', parent_id: null }

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/tutorials/7/edit']}>
      <Routes>
        <Route path="/tutorials/:id/edit" element={<EditTutorial />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  put.mockClear()
  apiGet.mockImplementation((url: string) => {
    if (url.endsWith('/nodes')) return Promise.resolve({ nodes: [NODE_A, NODE_B] })
    return Promise.resolve({
      id: 7,
      title: 'ComfyUI 入门',
      description: '',
      introduction: '',
      category: 'comfyui',
      status: 'updating',
      access_mode: 'free',
      points_price: 0,
      is_pinned: false,
      is_recommended: false,
      cover_image: '',
    })
  })
})

describe('章节切换前的脏内容保存', () => {
  it('键入新正文后切章，PUT 必须带上刚敲的内容', async () => {
    const user = userEvent.setup()
    renderPage()

    // 页面不自动选中章节：先点第一章，编辑器才会挂上它的内容
    await user.click(await screen.findByText(NODE_A.title))
    const editor = await screen.findByLabelText(NODE_EDITOR)
    await waitFor(() => expect(editor).toHaveProperty('value', NODE_A.content))

    await user.click(editor)
    await user.keyboard('新写的踩坑记录')

    await user.click(screen.getByText(NODE_B.title))

    await waitFor(() => expect(put).toHaveBeenCalled())
    const call = put.mock.calls[0]
    const body = call[1] as { title: string; content: string }
    expect(call[0]).toBe('/admin/tutorials/7/nodes/11')
    expect(body.title).toBe(NODE_A.title)
    expect(body.content).toContain('新写的踩坑记录')
  })

  it('未编辑时切章不发多余保存请求（避免每次点树都 PUT 一遍）', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByText(NODE_A.title))
    await waitFor(() => expect(screen.getByLabelText(NODE_EDITOR)).toHaveProperty('value', NODE_A.content))

    await user.click(screen.getByText(NODE_B.title))

    await waitFor(() => expect(screen.getByLabelText(NODE_EDITOR)).toHaveProperty('value', NODE_B.content))
    expect(put).not.toHaveBeenCalled()
  })
})
