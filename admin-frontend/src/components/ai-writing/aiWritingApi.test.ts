import { describe, expect, it, vi } from 'vitest'
import { streamWriting } from './aiWritingApi'
import { useAuthStore } from '../../store/authStore'

const encoder = new TextEncoder()

function responseFromChunks(chunks: Uint8Array[]) {
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

function responseFrom(frames: string[]) {
  return responseFromChunks([encoder.encode(frames.join(''))])
}

describe('streamWriting', () => {
  it('解析 delta/result/done 事件', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFrom([
      'event: meta\ndata: {"operation":"rewrite"}\n\n',
      'event: delta\ndata: {"text":"你"}\n\n',
      'event: delta\ndata: {"text":"好"}\n\n',
      'event: done\ndata: {"finish_reason":"stop"}\n\n',
    ])))
    const events: string[] = []
    await streamWriting(
      { operation: 'rewrite', content_type: 'node', context: { title: 'T' } },
      (e) => events.push(`${e.event}:${String(e.data.text ?? '')}`),
    )
    expect(events).toEqual(['delta:你', 'delta:好', 'done:'])
    vi.unstubAllGlobals()
  })

  it('HTTP 错误抛出后端文案', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ code: 429, message: '额度不足' }), { status: 429 },
    )))
    await expect(streamWriting(
      { operation: 'rewrite', content_type: 'node' }, () => {},
    )).rejects.toThrow('额度不足')
    vi.unstubAllGlobals()
  })

  it('FastAPI 422 的 detail 数组被拼成可读文案，而不是 [object Object]', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: [
        { type: 'string_too_long', loc: ['body', 'context', 'article_text'], msg: 'String should have at most 50000 characters' },
        { type: 'int_parsing', loc: ['body', 'target_length'], msg: 'Input should be a valid integer' },
      ],
    }), { status: 422 })))
    let error: Error | null = null
    try {
      await streamWriting({ operation: 'summarize', content_type: 'tutorial_node' }, () => {})
    } catch (err) {
      error = err as Error
    }
    expect(error).toBeInstanceOf(Error)
    expect(error?.message).toContain('body.context.article_text: String should have at most 50000 characters')
    expect(error?.message).toContain('body.target_length: Input should be a valid integer')
    expect(error?.message).not.toContain('[object Object]')
    vi.unstubAllGlobals()
  })

  it('CRLF 换行且帧跨 chunk 边界仍能解析', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFromChunks([
      encoder.encode('event: delta\r\ndata: {"text":"你"}\r\n\r\n'),
      encoder.encode('event: delta\r\ndata: {"text":"好"}\r'),
      encoder.encode('\n\r\n'),
      encoder.encode('event: done\r\ndata: {}\r\n\r\n'),
    ])))
    const events: string[] = []
    await streamWriting(
      { operation: 'rewrite', content_type: 'node' },
      (e) => events.push(`${e.event}:${String(e.data.text ?? '')}`),
    )
    expect(events).toEqual(['delta:你', 'delta:好', 'done:'])
    vi.unstubAllGlobals()
  })

  it('UTF-8 多字节字符被 chunk 边界切开时不乱码', async () => {
    const bytes = encoder.encode('event: delta\ndata: {"text":"写作"}\n\nevent: done\ndata: {}\n\n')
    // 按 5 字节硬切：中文字符（3 字节）会被切在中间
    const chunks: Uint8Array[] = []
    for (let i = 0; i < bytes.length; i += 5) chunks.push(bytes.slice(i, i + 5))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFromChunks(chunks)))
    const events: string[] = []
    await streamWriting(
      { operation: 'rewrite', content_type: 'node' },
      (e) => events.push(`${e.event}:${String(e.data.text ?? '')}`),
    )
    expect(events).toEqual(['delta:写作', 'done:'])
    vi.unstubAllGlobals()
  })

  it('流内 error 事件抛出后端文案（此前 delta 已下发）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFrom([
      'event: meta\ndata: {"operation":"rewrite"}\n\n',
      'event: delta\ndata: {"text":"你"}\n\n',
      'event: error\ndata: {"code":429,"message":"额度不足","retryable":false}\n\n',
      'event: done\ndata: {"finish_reason":"stop"}\n\n',
    ])))
    const events: string[] = []
    await expect(streamWriting(
      { operation: 'rewrite', content_type: 'node' },
      (e) => events.push(e.event),
    )).rejects.toThrow('额度不足')
    expect(events).toEqual(['delta'])
    vi.unstubAllGlobals()
  })

  it('流结束时没有空行收尾的最后一帧不丢', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFrom([
      'event: delta\ndata: {"text":"尾"}\n\n',
      'event: done\ndata: {"finish_reason":"stop"}',
    ])))
    const events: string[] = []
    await streamWriting(
      { operation: 'rewrite', content_type: 'node' },
      (e) => events.push(`${e.event}:${String(e.data.text ?? '')}`),
    )
    expect(events).toEqual(['delta:尾', 'done:'])
    vi.unstubAllGlobals()
  })

  it('流结束但没收到 done 时抛出「连接中断」（半截结果不能当成功）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFrom([
      'event: meta\ndata: {}\n\n',
      'event: delta\ndata: {"text":"写到一半"}\n\n',
    ])))
    await expect(streamWriting(
      { operation: 'rewrite', content_type: 'node' }, () => {},
    )).rejects.toThrow('连接中断，结果可能不完整')
    vi.unstubAllGlobals()
  })

  it('token 只进 Authorization 头，不进 URL；signal 透传给 fetch', async () => {
    const fetchMock = vi.fn().mockResolvedValue(responseFrom(['event: done\ndata: {}\n\n']))
    vi.stubGlobal('fetch', fetchMock)
    useAuthStore.setState({ token: 'tk-123' })
    const controller = new AbortController()
    await streamWriting({ operation: 'rewrite', content_type: 'node' }, () => {}, controller.signal)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/admin/ai/writing/run')
    expect(url).not.toContain('tk-123')
    expect(init.method).toBe('POST')
    expect(init.signal).toBe(controller.signal)
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tk-123')
    expect(JSON.parse(init.body as string)).toEqual({ operation: 'rewrite', content_type: 'node' })
    useAuthStore.setState({ token: null })
    vi.unstubAllGlobals()
  })

  it('响应不含帧分隔符时不会无限堆积缓冲', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFromChunks([
      encoder.encode(`event: delta\ndata: {"text":"${'x'.repeat(1_000_001)}`),
    ])))
    await expect(streamWriting(
      { operation: 'rewrite', content_type: 'node' }, () => {},
    )).rejects.toThrow('AI 响应格式异常')
    vi.unstubAllGlobals()
  })
})
