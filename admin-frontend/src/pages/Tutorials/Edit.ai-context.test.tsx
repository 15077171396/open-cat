/**
 * 教程页的 AI 上下文接线：
 * - 全文类操作发整本教程拼接正文，选中 / 续写类操作只发当前章节（Important 2）；
 * - 切章必须调用 ai.reset()（Important 7，防止 A 章结果应用到 B 章）。
 * QuillEditor 换成 textarea：jsdom 里不验证 Quill，只验证页面自己的数据流。
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { WritingOperation, WritingRunRequest } from '../../components/ai-writing/types'

const aiState = vi.hoisted(() => ({
  options: null as null | {
    buildBaseRequest: (operation: WritingOperation) => Omit<WritingRunRequest, 'operation'>
  },
  reset: vi.fn(),
}))

vi.mock('../../components/ai-writing/useAiWriting', () => ({
  useAiWriting: (options: typeof aiState.options) => {
    aiState.options = options
    return {
      turns: [],
      busy: false,
      open: false,
      setOpen: vi.fn(),
      run: vi.fn(),
      cancel: vi.fn(),
      reset: aiState.reset,
      regenerate: vi.fn(),
      applyTurn: vi.fn(),
    }
  },
}))

const apiGet = vi.fn(async (..._args: [string]) => ({}))
vi.mock('../../api/client', () => ({
  default: {
    put: vi.fn(async () => ({ data: {} })),
    post: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
  apiGet: (url: string) => apiGet(url),
  apiPost: vi.fn(async () => ({})),
}))

vi.mock('../../components/QuillEditor', () => ({
  default: ({
    value, onChange, placeholder,
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
  aiState.options = null
  aiState.reset.mockClear()
  apiGet.mockImplementation((url: string) => {
    if (url === '/admin/ai/writing/options') {
      return Promise.resolve({ enabled: false, operations: [] })
    }
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

describe('教程页 AI 上下文', () => {
  it('全文操作发整本教程，选中 / 续写操作只发当前章节', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByText(NODE_A.title))
    await waitFor(() => expect(screen.getByLabelText(NODE_EDITOR)).toHaveProperty('value', NODE_A.content))
    await waitFor(() => expect(aiState.options).not.toBeNull())

    const build = aiState.options!.buildBaseRequest
    const summarize = build('summarize')
    expect(summarize.context?.article_text).toContain('A 的原始正文')
    expect(summarize.context?.article_text).toContain('B 的原始正文')

    for (const operation of ['rewrite', 'polish', 'continue'] as const) {
      expect(build(operation).context?.article_text).toBe(NODE_A.content)
    }
  })

  it('切换章节会调用 ai.reset()，新上下文只包含新章节', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByText(NODE_A.title))
    await waitFor(() => expect(screen.getByLabelText(NODE_EDITOR)).toHaveProperty('value', NODE_A.content))
    const before = aiState.reset.mock.calls.length

    await user.click(screen.getByText(NODE_B.title))
    await waitFor(() => expect(screen.getByLabelText(NODE_EDITOR)).toHaveProperty('value', NODE_B.content))

    expect(aiState.reset.mock.calls.length).toBeGreaterThan(before)
    expect(aiState.options!.buildBaseRequest('continue').context?.article_text).toBe(NODE_B.content)
  })
})
