/**
 * AI 对话调试台冒烟：
 * 1. 拉取模型选项并默认选中启用的模型（默认模型优先）；
 * 2. 发送一条消息 → 流式 delta 增量渲染 + usage 摘要挂到最后一条回复上；
 * 3. 模型列表为空时给出引导文案（去「模型管理」配置）。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGetMock = vi.fn()
const streamMock = vi.fn()

vi.mock('../../api/client', () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}))

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    fetchPlaygroundOptions: (...args: unknown[]) => apiGetMock(...args),
    streamPlayground: (...args: unknown[]) => streamMock(...args),
  }
})

import AiPlaygroundPage from './index'

const OPTIONS = {
  items: [
    { code: 'm-default', display_name: '默认模型', provider: 'deepseek', provider_label: 'DeepSeek', is_enabled: true, is_default: true },
    { code: 'm-other', display_name: '备用模型', provider: 'openai', provider_label: 'OpenAI', is_enabled: true, is_default: false },
    { code: 'm-off', display_name: '停用模型', provider: 'qwen', provider_label: 'Qwen', is_enabled: false, is_default: false },
  ],
}

describe('AiPlayground', () => {
  beforeEach(() => {
    apiGetMock.mockReset().mockResolvedValue(OPTIONS)
    streamMock.mockReset()
  })

  it('渲染配置栏与空态，默认选中启用中的默认模型（停用项不进下拉）', async () => {
    render(<AiPlaygroundPage />)
    expect(await screen.findByText('AI 对话调试')).toBeInTheDocument()
    expect(screen.getByText('发送第一条消息，开始调试模型')).toBeInTheDocument()
    await waitFor(() => {
      expect(streamMock).not.toHaveBeenCalled()
    })
    // Select 的值由 options 驱动：默认模型在文档中可见（选中项渲染为 label）
    expect(await screen.findByText(/默认模型（DeepSeek）/)).toBeInTheDocument()
    // 停用模型不出现在下拉候选里
    expect(screen.queryByText(/停用模型/)).not.toBeInTheDocument()
  })

  it('发送后流式增量渲染并把 usage 摘要挂在回复上', async () => {
    type EventCb = (e: { event: string; data: Record<string, unknown> }) => void
    let onEvent: EventCb | null = null
    streamMock.mockImplementation(
      (_body: unknown, cb: EventCb, _signal: unknown) => {
        onEvent = cb
        cb({ event: 'meta', data: {} })
        cb({ event: 'delta', data: { text: '你' } })
        cb({ event: 'delta', data: { text: '好' } })
        cb({ event: 'usage', data: { prompt_tokens: 3, completion_tokens: 5, cost: 0.01 } })
        return Promise.resolve()
      },
    )
    render(<AiPlaygroundPage />)
    const input = await screen.findByPlaceholderText(/输入消息/)
    await userEvent.type(input, '测试消息')
    await userEvent.click(screen.getByRole('button', { name: /发\s*送/ }))

    await waitFor(() => {
      expect(streamMock).toHaveBeenCalled()
    })
    // 请求体：完整历史（用户消息），默认模型 code 被带上
    const [body] = streamMock.mock.calls[0] as [{ messages: { role: string; content: string }[]; model_code?: string }]
    expect(body.messages).toEqual([{ role: 'user', content: '测试消息' }])
    expect(body.model_code).toBe('m-default')

    // delta 增量拼接 + usage 摘要（沿用真实回调闭包，不依赖 mock 时序）
    await waitFor(() => {
      expect(screen.getByText('你好')).toBeInTheDocument()
      expect(screen.getByText(/3 \+ 5 tokens/)).toBeInTheDocument()
    })
    expect(onEvent).not.toBeNull()
  })

  it('没有可用模型时下拉空态给引导文案', async () => {
    apiGetMock.mockResolvedValue({ items: [] })
    const { container } = render(<AiPlaygroundPage />)
    // notFoundContent 只在下拉展开时渲染；antd Select 以 mousedown 开启下拉
    // （官方测试口径；antd v6 的 Select 根节点就是 .ant-select，没有 v5 的 -selector 层）
    await screen.findByText('选择文本模型')
    fireEvent.mouseDown(container.querySelector('.ant-select') as HTMLElement)
    expect(await screen.findByText('暂无可用模型，请先到「模型管理」配置')).toBeInTheDocument()
  })
})
