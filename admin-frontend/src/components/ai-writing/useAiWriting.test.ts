import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { buildWritingHistory, useAiWriting, type WritingTurn } from './useAiWriting'
import type { WritingRunRequest, WritingSseEvent } from './types'

vi.mock('../../utils/toast', () => ({
  toast: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() },
}))

import { toast } from '../../utils/toast'

const bridge = {
  getSelection: () => ({ index: 0, length: 2, text: '原文' }),
  getCaret: () => 2,
  getFullText: () => 'abcdef',
  getSemanticHtml: () => '<p>abcdef</p>',
  getOutline: () => ['标题'],
  replaceRange: vi.fn(() => true),
  insertHtml: vi.fn(),
  insertImage: vi.fn(),
  dispose: vi.fn(),
}

function fakeStream(events: WritingSseEvent[]) {
  return async (
    _body: unknown,
    onEvent: (event: WritingSseEvent) => void,
  ) => {
    for (const event of events) onEvent(event)
  }
}

function makeTurn(output: string, patch: Partial<WritingTurn> = {}): WritingTurn {
  return {
    id: `t-${output.slice(0, 4)}-${Math.random()}`,
    operation: 'rewrite',
    request: { operation: 'rewrite', content_type: 'node' },
    output,
    result: null,
    images: [],
    status: '已完成',
    error: '',
    running: false,
    ...patch,
  }
}

describe('useAiWriting', () => {
  beforeEach(() => {
    vi.mocked(toast.error).mockClear()
    vi.mocked(toast.warning).mockClear()
  })

  it('流式追加 delta 并可取消', async () => {
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: bridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: fakeStream([
        { event: 'meta', data: {} },
        { event: 'delta', data: { text: '你' } },
        { event: 'delta', data: { text: '好' } },
        { event: 'done', data: {} },
      ]) as never,
    }))
    await act(async () => { await result.current.run('rewrite') })
    await waitFor(() => expect(result.current.turns[0].output).toBe('你好'))
    expect(result.current.turns[0].status).toBe('已完成')
  })

  it('buildBaseRequest 收到当前操作（教程页据此区分全文 / 单章）', async () => {
    const buildBaseRequest = vi.fn(() => ({ content_type: 'node' as const }))
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: bridge },
      buildBaseRequest,
      stream: fakeStream([{ event: 'done', data: {} }]) as never,
    }))
    await act(async () => { await result.current.run('summarize') })
    expect(buildBaseRequest).toHaveBeenCalledWith('summarize')
  })

  it('页面卸载时中止在途请求', async () => {
    let captured: AbortSignal | undefined
    const stream = vi.fn((
      _body: unknown,
      _onEvent: (event: WritingSseEvent) => void,
      signal: AbortSignal,
    ) => {
      captured = signal
      // 真实 SSE 在 abort 时会以 AbortError 拒绝；这里同样拒绝，覆盖 run 的收尾路径。
      return new Promise<void>((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'))
        })
      })
    })

    const { result, unmount } = renderHook(() => useAiWriting({
      bridgeRef: { current: bridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: stream as never,
    }))

    act(() => { void result.current.run('rewrite') })
    expect(stream).toHaveBeenCalledTimes(1)
    expect(captured?.aborted).toBe(false)

    // unmount 由 RTL 包在 act 内；顺带把 AbortError 收尾的微任务在当前 act 内跑完。
    await act(async () => {
      unmount()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(captured?.aborted).toBe(true)
  })

  it('采纳时替换原选区', async () => {
    const replace = vi.fn(() => true)
    const localBridge = { ...bridge, replaceRange: replace }
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: localBridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: fakeStream([
        { event: 'delta', data: { text: '新文' } },
        { event: 'done', data: {} },
      ]) as never,
    }))
    await act(async () => { await result.current.run('rewrite') })
    act(() => { result.current.applyTurn(result.current.turns[0].id, 'replace') })
    expect(replace).toHaveBeenCalled()
  })

  it('续写没有选区时用当前光标计算 before / after', async () => {
    const bodies: WritingRunRequest[] = []
    const noSelectionBridge = {
      ...bridge,
      getSelection: () => null,
      getCaret: () => 3,
      getFullText: () => 'abcdef',
    }
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: noSelectionBridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: (async (body: WritingRunRequest, onEvent: (event: WritingSseEvent) => void) => {
        bodies.push(body)
        onEvent({ event: 'done', data: {} })
      }) as never,
    }))

    await act(async () => { await result.current.run('continue') })
    expect(bodies[0].selection).toBeNull()
    expect(bodies[0].context?.before).toBe('abc')
    expect(bodies[0].context?.after).toBe('def')
    expect(bodies[0].context?.article_text).toBe('abcdef')
  })

  it('光标移动后续写落点跟着更新（不复用上一次位置）', async () => {
    const bodies: WritingRunRequest[] = []
    let caret = 2
    const caretBridge = {
      ...bridge,
      getSelection: () => null,
      getCaret: () => caret,
      getFullText: () => 'abcdef',
    }
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: caretBridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: (async (body: WritingRunRequest, onEvent: (event: WritingSseEvent) => void) => {
        bodies.push(body)
        onEvent({ event: 'done', data: {} })
      }) as never,
    }))

    await act(async () => { await result.current.run('continue') })
    expect(bodies[0].context?.before).toBe('ab')

    caret = 4
    await act(async () => { await result.current.run('continue') })
    expect(bodies[1].context?.before).toBe('abcd')
    expect(bodies[1].context?.after).toBe('ef')
  })

  it('光标与选区都不存在时续写提示定位，不发请求', async () => {
    const stream = vi.fn()
    const emptyBridge = { ...bridge, getSelection: () => null, getCaret: () => null }
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: emptyBridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: stream as never,
    }))

    await act(async () => { await result.current.run('continue') })
    expect(stream).not.toHaveBeenCalled()
    expect(result.current.turns).toHaveLength(0)
    expect(toast.error).toHaveBeenCalledWith('请先在正文中定位光标后再续写')
  })

  it('改写 / 润色 / 扩写 / 缩写 没有选区时提示并不发送', async () => {
    const stream = vi.fn()
    const noSelectionBridge = { ...bridge, getSelection: () => null }
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: noSelectionBridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: stream as never,
    }))

    for (const operation of ['rewrite', 'polish', 'expand', 'shorten'] as const) {
      await act(async () => { await result.current.run(operation) })
    }
    expect(stream).not.toHaveBeenCalled()
    expect(result.current.turns).toHaveLength(0)
    expect(toast.error).toHaveBeenCalledWith('请先选中要处理的文字')
  })

  it('请求体带上补充要求、目标字数与已完成回合的历史', async () => {
    const bodies: WritingRunRequest[] = []
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: bridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: (async (body: WritingRunRequest, onEvent: (event: WritingSseEvent) => void) => {
        bodies.push(body)
        onEvent({ event: 'delta', data: { text: bodies.length === 1 ? '初稿' : '复稿' } })
        onEvent({ event: 'done', data: {} })
      }) as never,
    }))

    await act(async () => { await result.current.run('rewrite') })
    await act(async () => {
      await result.current.run('expand', {
        instruction: '  更口语化  ', targetLength: 300, modelCode: 'qwen',
      })
    })

    expect(bodies[0].instruction).toBeUndefined()
    expect(bodies[0].target_length).toBeNull()
    expect(bodies[0].history).toEqual([])
    expect(bodies[0].model_code).toBeUndefined()

    expect(bodies[1].instruction).toBe('更口语化')
    expect(bodies[1].target_length).toBe(300)
    expect(bodies[1].model_code).toBe('qwen')
    expect(bodies[1].history).toEqual([
      { role: 'user', content: '执行「rewrite」操作' },
      { role: 'assistant', content: '初稿' },
    ])
  })

  it('SSE 未收到 done 而结束（客户端抛错）时回合标记失败', async () => {
    const stream = vi.fn(async () => {
      throw new Error('连接中断，结果可能不完整')
    })
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: bridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: stream as never,
    }))

    await act(async () => { await result.current.run('rewrite') })
    expect(result.current.turns[0].status).toBe('失败')
    expect(result.current.turns[0].error).toBe('连接中断，结果可能不完整')
    expect(result.current.turns[0].running).toBe(false)
    expect(toast.error).toHaveBeenCalledWith('连接中断，结果可能不完整')
  })

  it('reset 中止在途请求、清空回合并关闭面板（切章防串台）', async () => {
    let captured: AbortSignal | undefined
    const stream = vi.fn((
      _body: unknown,
      _onEvent: (event: WritingSseEvent) => void,
      signal: AbortSignal,
    ) => {
      captured = signal
      return new Promise<void>((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'))
        })
      })
    })
    const { result } = renderHook(() => useAiWriting({
      bridgeRef: { current: bridge },
      buildBaseRequest: () => ({ content_type: 'node' }),
      stream: stream as never,
    }))

    act(() => { void result.current.run('rewrite') })
    expect(result.current.open).toBe(true)
    expect(result.current.turns).toHaveLength(1)

    await act(async () => {
      result.current.reset()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(captured?.aborted).toBe(true)
    expect(result.current.turns).toHaveLength(0)
    expect(result.current.open).toBe(false)
    expect(result.current.busy).toBe(false)
  })
})

describe('buildWritingHistory', () => {
  it('只取最近 3 轮（最多 6 条 user/assistant 消息）', () => {
    const history = buildWritingHistory([
      makeTurn('第一轮'),
      makeTurn('第二轮'),
      makeTurn('第三轮'),
      makeTurn('第四轮'),
    ])
    expect(history).toHaveLength(6)
    expect(history[1]).toEqual({ role: 'assistant', content: '第二轮' })
    expect(history[5]).toEqual({ role: 'assistant', content: '第四轮' })
  })

  it('总字符超过 4000 时丢弃最旧的整轮，不拆散 user/assistant 配对', () => {
    const history = buildWritingHistory([
      makeTurn('a'.repeat(1500)),
      makeTurn('b'.repeat(1500)),
      makeTurn('c'.repeat(1500)),
    ])
    const total = history.reduce((sum, item) => sum + item.content.length, 0)
    expect(total).toBeLessThanOrEqual(4000)
    expect(history).toHaveLength(4)
    expect(history[0].role).toBe('user')
    expect(history[3]).toEqual({ role: 'assistant', content: 'c'.repeat(1500) })
  })

  it('最新一轮单独超预算时保留提问并截断回答', () => {
    const history = buildWritingHistory([makeTurn('x'.repeat(8000))])
    const total = history.reduce((sum, item) => sum + item.content.length, 0)
    expect(total).toBeLessThanOrEqual(4000)
    expect(history).toHaveLength(2)
    expect(history[1].content.endsWith('…')).toBe(true)
  })

  it('跳过失败 / 运行中 / 无产出的回合', () => {
    const history = buildWritingHistory([
      makeTurn('失败内容', { status: '失败', error: 'boom' }),
      makeTurn('', { status: '生成中', running: true }),
      makeTurn('', { status: '已完成' }),
      makeTurn('可用内容'),
    ])
    expect(history).toEqual([
      { role: 'user', content: '执行「rewrite」操作' },
      { role: 'assistant', content: '可用内容' },
    ])
  })
})
