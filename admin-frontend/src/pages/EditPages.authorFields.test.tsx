/**
 * 「编辑页必须把加载到的作者字段原样存回去」的回归测试。
 * validateFields() 只回传已注册字段，未注册的键提交后读成 undefined → 空串 → 后端按「清除」
 * 处理成 NULL（头像上传成功却存不上、只改标题会顺手抹掉封面）。只测「加载 → 直接保存 →
 * PUT body 字段仍在」这条最易出事的链路，不测上传。
 */

import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const put = vi.fn(async (..._args: [string, unknown]) => ({ data: {} }))
const apiGet = vi.fn(async (..._args: [string]) => ({}))

// 本文件在 src/pages/ 下（不是某个页面目录内），相对路径比同目录的页面测试少一层
vi.mock('../api/client', () => ({
  default: {
    put: (url: string, body: unknown) => put(url, body),
    post: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
  apiGet: (url: string) => apiGet(url),
  apiPost: vi.fn(async () => ({})),
  apiPut: (url: string, body: unknown) => put(url, body),
}))

// 富文本编辑器换成受控 textarea：jsdom 下 Quill 内核行为不可信（见 vitest.config.ts 说明），
// 这里要验的是页面自己的数据流。
vi.mock('../components/QuillEditor', () => ({
  default: ({ value, placeholder }: { value?: string; placeholder?: string }) => (
    <textarea aria-label={placeholder || 'editor'} value={value ?? ''} readOnly />
  ),
}))

// 布局壳替身：真实 EditPageLayout 的保存按钮在本测试之外，这里直接暴露 onSave
vi.mock('../components/EditPageLayout', () => ({
  default: ({ children, onSave }: { children?: React.ReactNode; onSave?: () => void }) => (
    <div>
      {children}
      <button type="button" onClick={onSave}>
        保存
      </button>
    </div>
  ),
}))
vi.mock('../components/NetdiskLinksEditor', () => ({ default: () => null }))
vi.mock('../components/PluginSourcesEditor', () => ({ default: () => null }))
vi.mock('../components/ThirdPartyImport', () => ({
  default: () => null,
  buildMetaPatch: () => ({}),
}))
vi.mock('../hooks/useCategoryOptions', () => ({ useCategoryOptions: () => [] }))

import NodesEdit from './Nodes/Edit'
import ResourcesEdit from './Resources/Edit'
import PluginsEdit from './Plugins/Edit'

/** 每个域的编辑页：挂载路径 + 后端详情地址 + 期望的 PUT 地址 */
const CASES = [
  {
    name: '百科节点',
    path: '/nodes/7/edit',
    route: '/nodes/:id/edit',
    Page: NodesEdit,
    detailUrl: '/admin/nodes/7',
    putUrl: '/admin/nodes/7',
    detail: {
      id: 7,
      name: 'KSampler',
      category: 'comfyui',
      description: '采样器',
      content: '# 正文',
      cover_image: 'https://cdn.example.com/cover.png',
      author_name: '张三',
      author_avatar_url: 'https://cdn.example.com/avatar.png',
    },
  },
  {
    name: '资源',
    path: '/resources/7/edit',
    route: '/resources/:id/edit',
    Page: ResourcesEdit,
    detailUrl: '/admin/resources/7',
    putUrl: '/admin/resources/7',
    detail: {
      id: 7,
      name: 'ComfyUI 工作流包',
      description: '简介',
      content: '正文',
      resource_type: 'workflow',
      file_size: '',
      is_featured: false,
      is_pinned: false,
      cover_image: 'https://cdn.example.com/cover.png',
      author_name: '张三',
      author_avatar_url: 'https://cdn.example.com/avatar.png',
      // 标签是必填项（「请至少添加一个标签」），留空会让保存停在表单校验而发不出 PUT
      tags: ['工作流'],
    },
  },
  {
    name: '扩展',
    path: '/plugins/7/edit',
    route: '/plugins/:id/edit',
    Page: PluginsEdit,
    detailUrl: '/admin/plugins/7',
    putUrl: '/admin/plugins/7',
    detail: {
      id: 7,
      name: 'ComfyUI Manager',
      category: 'comfyui',
      description: '简介',
      content: '正文',
      author_name: '张三',
      author_avatar_url: 'https://cdn.example.com/avatar.png',
      sources: [],
    },
  },
] as const

function renderAt(route: string, path: string, Page: () => JSX.Element) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={route} element={<Page />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  put.mockClear()
  apiGet.mockImplementation((url: string) => {
    const hit = CASES.find((c) => c.detailUrl === url)
    return Promise.resolve(hit ? { ...hit.detail } : {})
  })
})

describe('编辑页的作者/封面字段往返（R5-01）', () => {
  it.each(CASES.map((c) => [c.name, c] as const))('%s：不改作者字段直接保存，PUT 必须原样带回', async (_label, c) => {
    renderAt(c.route, c.path, c.Page as () => JSX.Element)

    // 等详情回填完成（名称字段出现原值即表示 setFieldsValue 已跑过）
    await waitFor(() => expect(screen.getByDisplayValue(c.detail.name)).toBeTruthy())

    await screen.findByRole('button', { name: '保存' })
    await waitFor(() => {
      const nameField = screen.queryByDisplayValue(c.detail.name)
      expect(nameField).toBeTruthy()
    })
    ;(screen.getByRole('button', { name: '保存' }) as HTMLElement).click()

    await waitFor(() => expect(put).toHaveBeenCalled())
    const [url, body] = put.mock.calls[0]
    expect(url).toBe(c.putUrl)
    const payload = body as Record<string, unknown>
    expect(payload.author_name).toBe('张三')
    expect(payload.author_avatar_url).toBe('https://cdn.example.com/avatar.png')
    if ('cover_image' in c.detail) {
      expect(payload.cover_image).toBe('https://cdn.example.com/cover.png')
    }
  })
})
