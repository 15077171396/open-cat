import { apiGet } from '../../api/client'
import { useAuthStore } from '../../store/authStore'

const API_PATH = '/api/v1/admin/ai/playground/run'

// 正常帧只有几百字节。超过这个量说明对端不再发帧分隔符，继续拼 buffer
// 就是无上限增长（内存泄漏），按协议错误中止 —— 与 AI 写作同口径。
const MAX_FRAME_CHARS = 1_000_000

export interface PlaygroundModelOption {
  code: string
  display_name: string
  provider: string
  provider_label: string
  is_enabled: boolean
  is_default: boolean
}

export interface PlaygroundOptions {
  items: PlaygroundModelOption[]
}

export interface PlaygroundTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface PlaygroundRunRequest {
  model_code?: string
  system_prompt?: string
  messages: PlaygroundTurn[]
  temperature?: number
  max_tokens?: number
}

export type PlaygroundEventName = 'meta' | 'delta' | 'usage' | 'done' | 'error'

export interface PlaygroundEvent {
  event: PlaygroundEventName
  data: Record<string, unknown>
}

export async function fetchPlaygroundOptions(): Promise<PlaygroundOptions> {
  return apiGet<PlaygroundOptions>('/admin/ai/playground/options')
}

/** FastAPI 422 的 `detail` 是对象数组；直接 String() 会得到 `[object Object]` */
function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return ''
  const parts = detail.map((item) => {
    if (!item || typeof item !== 'object') return ''
    const entry = item as { msg?: unknown }
    return typeof entry.msg === 'string' ? entry.msg : ''
  }).filter(Boolean)
  return parts.length ? `请求参数有误：${parts.join('；')}` : ''
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { message?: unknown; detail?: unknown }
    if (typeof body?.message === 'string' && body.message) return body.message
    const detail = formatDetail(body?.detail)
    if (detail) return detail
    return `请求失败（${response.status}）`
  } catch {
    return `请求失败（${response.status}）`
  }
}

/** 解析一帧（`event:` + `data:`）。坏帧返回 null：流里混进一个坏帧不该作废整段内容。 */
function parseFrame(frame: string): PlaygroundEvent | null {
  let eventName = ''
  const dataLines: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!eventName || dataLines.length === 0) return null
  try {
    const data: unknown = JSON.parse(dataLines.join('\n'))
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null
    return {
      event: eventName as PlaygroundEventName,
      data: data as Record<string, unknown>,
    }
  } catch {
    return null
  }
}

/**
 * 发起对话流并逐帧回调。用 fetch 而不是 EventSource：EventSource 不能 POST /
 * 带请求体 / 带 Authorization header（与 AI 写作同一取舍）。
 * HTTP 阶段失败抛后端 `message`；流开始后的失败由后端 `event: error` 抛成 Error；
 * 只有收到 done 才算完整结束，网关截断会被识别为「连接中断」。
 */
export async function streamPlayground(
  body: PlaygroundRunRequest,
  onEvent: (event: PlaygroundEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = useAuthStore.getState().token
  const response = await fetch(API_PATH, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) throw new Error(await errorMessage(response))
  if (!response.body) throw new Error('浏览器不支持流式响应')

  let sawDone = false
  const dispatch = (frame: string) => {
    const parsed = parseFrame(frame)
    if (!parsed) return
    if (parsed.event === 'error') {
      throw new Error(String((parsed.data as { message?: unknown }).message || 'AI 请求失败'))
    }
    if (parsed.event === 'done') sawDone = true
    onEvent(parsed)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split(/\r?\n\r?\n/)
      // 最后一段可能是半帧（分块边界落在帧中间），留到下一轮再拼
      buffer = frames.pop() ?? ''
      for (const frame of frames) dispatch(frame)
      if (buffer.length > MAX_FRAME_CHARS) throw new Error('AI 响应格式异常，请重试')
    }
    // 流结束时最后一段可能没有空行收尾；真被截断的帧会因 JSON 坏在 parseFrame 被跳过
    buffer += decoder.decode()
    if (buffer.trim()) dispatch(buffer)
    if (!sawDone) throw new Error('连接中断，结果可能不完整')
  } catch (err) {
    // 提前退出（error 事件 / 格式异常 / 调用方 abort）时主动断开，否则后端会继续生成
    await reader.cancel().catch(() => {})
    throw err
  }
}
