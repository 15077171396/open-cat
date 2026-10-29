import { useAuthStore } from '../../store/authStore'
import type { WritingRunRequest, WritingSseEvent } from './types'

const API_PATH = '/api/v1/admin/ai/writing/run'

// 正常帧只有几百字节（一条 delta 就是一小段文本）。超过这个量说明对端不再发帧分隔符，
// 继续往 buffer 里拼就是无上限增长（浏览器内存泄漏），按协议错误中止。
const MAX_FRAME_CHARS = 1_000_000

/** FastAPI 422 的 `detail` 是对象数组；直接 String() 会得到 `[object Object]`，这里拼成可读文案 */
function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return ''
  const parts = detail.map((item) => {
    if (typeof item === 'string') return item
    if (!item || typeof item !== 'object') return ''
    const entry = item as { loc?: unknown; msg?: unknown }
    const loc = Array.isArray(entry.loc) ? entry.loc.map(String).filter(Boolean).join('.') : ''
    const msg = typeof entry.msg === 'string' ? entry.msg : ''
    if (loc && msg) return `${loc}: ${msg}`
    return msg || loc
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

/** 解析一帧（`event:` + `data:` 行）。缺字段、JSON 坏掉的帧返回 null 而不是抛错：
 *  流里混进一个坏帧不该让整段已生成的内容作废。 */
function parseFrame(frame: string): WritingSseEvent | null {
  let eventName = ''
  const dataLines: string[] = []
  // 逐行扫描而非 find：SSE 允许 data 分多行（按规范用 \n 拼接），
  // 顺便自然忽略 `: ping` 注释、`id:` / `retry:` 这类本接口不用的字段。
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!eventName || dataLines.length === 0) return null

  let data: Record<string, unknown> = {}
  try {
    const parsed: unknown = JSON.parse(dataLines.join('\n'))
    // 后端约定 data 恒为对象；非对象按空对象处理，避免下游 `data.text` 取到字符串下标之类的东西
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      data = parsed as Record<string, unknown>
    }
  } catch {
    return null
  }
  return { event: eventName as WritingSseEvent['event'], data }
}

/**
 * 发起 AI 写作流并逐帧回调。
 *
 * 用 fetch 而不是 EventSource：EventSource 只能 GET、不能带请求体，而这里的入参是整段上下文。
 * HTTP 阶段失败抛后端 `message`；流已开始后的失败由后端发 `event: error`，同样抛成 Error，
 * 调用方用 try/catch 统一处理即可。取消走传入的 AbortSignal。
 */
export async function streamWriting(
  body: WritingRunRequest,
  onEvent: (event: WritingSseEvent) => void,
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

  // 只有收到 done 才算完整结束：网关截断 / 后端未收尾时 TCP 也会「正常」EOF，
  // 不区分的话半截结果会被当成成功（还带着一个假的「已完成」状态）。
  let sawDone = false
  const dispatch = (frame: string) => {
    const parsed = parseFrame(frame)
    if (!parsed) return
    if (parsed.event === 'error') {
      throw new Error(String(parsed.data.message || 'AI 请求失败'))
    }
    // meta 只回带 request_id / operation（后端日志里的同款信息），面板没有消费方：
    // 下发只会白跑一次渲染，还把请求标识混进 UI 状态。
    if (parsed.event === 'meta') return
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
      // 最后一段可能是半帧（分块边界正好落在帧中间），留到下一轮再拼
      buffer = frames.pop() ?? ''
      for (const frame of frames) dispatch(frame)
      if (buffer.length > MAX_FRAME_CHARS) throw new Error('AI 响应格式异常，请重试')
    }
    // 流结束时最后一段可能没有空行收尾，直接丢掉的话 usage/done 就静默丢了；
    // 真被截断的帧会因 JSON 解析失败在 parseFrame 里被跳过，不会误报。
    buffer += decoder.decode()
    if (buffer.trim()) dispatch(buffer)
    if (!sawDone) throw new Error('连接中断，结果可能不完整')
  } catch (err) {
    // 提前退出（error 事件 / 格式异常 / 调用方 abort）时主动断开：
    // 否则后端会继续生成到自然结束，额度白烧。
    await reader.cancel().catch(() => {})
    throw err
  }
}
