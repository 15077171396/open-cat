/** 站内聊天 API：REST + WebSocket 封装（自动重连）。 */

export interface ChatMessage {
  id: number
  conversation_id: number
  sender_id: number
  content: string
  created_at: string | null
}

export interface ChatMemberBrief {
  id: number
  account: string | null
  nickname: string | null
}

export interface ChatConversation {
  id: number
  type: 'direct' | 'group'
  title: string
  member_ids: number[]
  profiles: ChatMemberBrief[]
  last_message: ChatMessage | null
  unread: number
  updated_at: string | null
}

export interface ChatConversationsData {
  items: ChatConversation[]
  unread_total: number
}

export interface ChatMessagesData {
  items: ChatMessage[]
  has_more: boolean
}

export async function fetchConversations(): Promise<ChatConversationsData> {
  const resp = await fetch('/api/v1/admin/chat/conversations')
  const body = await resp.json()
  return body.data as ChatConversationsData
}

export async function fetchMessages(conversationId: number, beforeId?: number): Promise<ChatMessagesData> {
  const qs = beforeId != null ? `?before_id=${beforeId}` : ''
  const resp = await fetch(`/api/v1/admin/chat/conversations/${conversationId}/messages${qs}`)
  const body = await resp.json()
  return body.data as ChatMessagesData
}

export async function sendMessage(conversationId: number, content: string): Promise<ChatMessage> {
  const resp = await fetch(`/api/v1/admin/chat/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  const body = await resp.json()
  if (!resp.ok) throw new Error(body?.message || '发送失败')
  return body.data as ChatMessage
}

export async function openDirect(userId: number): Promise<{ id: number; created: boolean }> {
  const resp = await fetch(`/api/v1/admin/chat/direct/${userId}`, { method: 'POST' })
  const body = await resp.json()
  if (!resp.ok) throw new Error(body?.message || '发起失败')
  return body.data
}

export async function createGroup(title: string, memberIds: number[]): Promise<{ id: number; created: boolean }> {
  const resp = await fetch('/api/v1/admin/chat/group', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, member_ids: memberIds }),
  })
  const body = await resp.json()
  if (!resp.ok) throw new Error(body?.message || '建群失败')
  return body.data
}

export async function markRead(conversationId: number): Promise<void> {
  await fetch(`/api/v1/admin/chat/conversations/${conversationId}/read`, { method: 'POST' })
}

export type ChatSocketEvent =
  | { type: 'message'; conversation_id: number; message: ChatMessage }

/**
 * 打开聊天 WS 并自动重连（指数退避，30s 封顶）。
 *
 * 鉴权靠 httpOnly cookie（同源握手自动携带）——不传 token 参数，
 * URL 会进访问日志，等于把凭据写进磁盘。
 */
export function openChatSocket(onEvent: (event: ChatSocketEvent) => void): () => void {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
  let socket: WebSocket | null = null
  let retry = 0
  let closed = false
  let timer: number | undefined

  const connect = () => {
    if (closed) return
    socket = new WebSocket(`${scheme}://${location.host}/api/v1/admin/ws/chat`)
    socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data as string)
        if (parsed && typeof parsed === 'object' && parsed.type === 'message') {
          onEvent(parsed as ChatSocketEvent)
        }
      } catch {
        // 坏帧忽略：实时通道允许丢帧（历史接口兜底）
      }
    }
    socket.onopen = () => {
      retry = 0
      // 空闲保活：穿透代理的读超时（nginx 3600s，本地 dev 更短）
      timer = window.setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) socket.send('ping')
      }, 25_000)
    }
    socket.onclose = () => {
      if (timer) window.clearInterval(timer)
      if (closed) return
      retry += 1
      const delay = Math.min(30_000, 1_000 * 2 ** Math.min(retry, 5))
      window.setTimeout(connect, delay)
    }
  }
  connect()

  return () => {
    closed = true
    if (timer) window.clearInterval(timer)
    socket?.close()
  }
}
