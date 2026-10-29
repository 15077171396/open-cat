// Edit.ai.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  useParams: () => ({ id: '1' }),
}))
vi.mock('../../api/client', () => ({
  apiGet: vi.fn((url: string) => {
    if (url.includes('ai/writing/options')) {
      return Promise.resolve({ enabled: true, operations: [], text_models: [], image_models: [], image_model: null, styles: [], sizes: [], image_daily_limit: 30, image_used_today: 0, hourly_limit: 60 })
    }
    if (url.includes('/categories')) return Promise.resolve([])
    return Promise.resolve({ id: 1, name: '节点', category: '基础', description: '', content: '', cover_image: '', author_name: '', author_avatar_url: '', seo_title: '', seo_description: '', seo_keywords: '', is_published: true })
  }),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
}))
vi.mock('../../components/QuillEditor', () => ({
  default: () => <div data-testid="quill" />,
}))
// 面板行为由 Task 11 的组件测试覆盖；这里只验页面接线，同时避免 antd 6 Drawer
// 的弃用告警（width → size，属组件自身遗留）污染挂载测试输出
vi.mock('../../components/ai-writing/AiWritingDrawer', () => ({
  default: () => <div data-testid="ai-drawer" />,
}))

import { apiGet } from '../../api/client'
import NodesEdit from './Edit'

describe('NodesEdit AI 入口', () => {
  it('挂载不报错并加载 AI 配置', async () => {
    render(<NodesEdit />)
    expect(screen.getByTestId('quill')).toBeInTheDocument()
    // 入口是否可用取决于配置接口：未接入 AI 时页面不会请求该地址
    await waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith('/admin/ai/writing/options')
    })
    // 配置就绪后才挂载 AI 面板
    expect(await screen.findByTestId('ai-drawer')).toBeInTheDocument()
    // 等详情回填完成，避免异步 setState 落在 act 之外
    await screen.findByDisplayValue('节点')
  })
})
