import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Drawer, Form, Input, InputNumber, Tag } from 'antd'
import {
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { useAuthStore } from '../../store/authStore'
import { toast } from '../../utils/toast'
import { getErrorMessage, isFormValidationError } from '../../utils/error'
import { formatTime } from '../../utils/datetime'
import PageHeader from '../../components/ui/PageHeader'
import { ModalForm } from '../../components/ui'
import {
  createGroup,
  fetchConversations,
  fetchMessages,
  markRead,
  openChatSocket,
  openDirect,
  sendMessage,
  type ChatConversation,
  type ChatMessage,
  type ChatSocketEvent,
} from './api'

/**
 * 站内聊天（管理员间私聊/群聊）：左会话列表 + 右消息区。
 *
 * 实时性由 WS 推送（断线自动重连）；可靠性由 REST 兜底 —— 发消息走 HTTP，
 * 断线期间的消息重连后拉历史补齐。范围刻意轻量：纯文字，不做文件/撤回/
 * 已读回执（见后端 models/chat.py 的边界声明）。
 */
export default function ChatPage() {
  const me = useAuthStore((s) => s.user)
  const [conversations, setConversations] = useState<ChatConversation[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [listOpen, setListOpen] = useState(false)
  const [groupOpen, setGroupOpen] = useState(false)
  const [directOpen, setDirectOpen] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef<number | null>(null)
  activeRef.current = activeId

  const loadConversations = useCallback(async () => {
    try {
      const data = await fetchConversations()
      setConversations(data.items)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '加载会话失败')
    }
  }, [])

  const openConversation = useCallback(async (convId: number) => {
    setActiveId(convId)
    setListOpen(false)
    setLoading(true)
    try {
      const data = await fetchMessages(convId)
      setMessages(data.items)
      setHasMore(data.has_more)
      await markRead(convId)
      setConversations((prev) =>
        prev.map((c) => (c.id === convId ? { ...c, unread: 0 } : c)),
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '加载消息失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadOlder = useCallback(async () => {
    if (!activeId || !messages.length) return
    try {
      const data = await fetchMessages(activeId, messages[0].id)
      setMessages((prev) => [...data.items, ...prev])
      setHasMore(data.has_more)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '加载更多失败')
    }
  }, [activeId, messages])

  useEffect(() => {
    void loadConversations()
  }, [loadConversations])

  // 实时推送：当前会话追加 + 已读；其他会话未读 +1 并刷新列表
  useEffect(() => {
    const close = openChatSocket((event: ChatSocketEvent) => {
      if (event.type !== 'message') return
      if (activeRef.current === event.conversation_id) {
        setMessages((prev) =>
          prev.some((m) => m.id === event.message.id) ? prev : [...prev, event.message],
        )
        void markRead(event.conversation_id)
      } else {
        setConversations((prev) =>
          prev.map((c) =>
            c.id === event.conversation_id
              ? { ...c, unread: c.unread + 1, last_message: event.message }
              : c,
          ),
        )
      }
    })
    return close
  }, [])

  // 新消息到达滚到底部
  useEffect(() => {
    const el = scrollRef.current
    el?.scrollTo?.({ top: el.scrollHeight })
  }, [messages])

  const handleSend = useCallback(async () => {
    const content = input.trim()
    if (!activeId || !content || sending) return
    setSending(true)
    try {
      const message = await sendMessage(activeId, content)
      setMessages((prev) =>
        prev.some((m) => m.id === message.id) ? prev : [...prev, message],
      )
      setInput('')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '发送失败')
    } finally {
      setSending(false)
    }
  }, [activeId, input, sending])

  const handleOpenDirect = useCallback(async (values: Record<string, unknown>) => {
    const userId = Number(values.user_id)
    if (!Number.isInteger(userId) || userId <= 0) return
    try {
      const result = await openDirect(userId)
      await loadConversations()
      setDirectOpen(false)
      await openConversation(result.id)
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '发起失败'))
    }
  }, [loadConversations, openConversation])

  const handleCreateGroup = useCallback(async (values: Record<string, unknown>) => {
    try {
      const ids: number[] = String(values.member_ids ?? '')
        .split(/[,\s，]+/)
        .map((v: string) => Number(v.trim()))
        .filter((v: number) => Number.isInteger(v) && v > 0)
      const result = await createGroup(String(values.title ?? ''), ids)
      await loadConversations()
      setGroupOpen(false)
      await openConversation(result.id)
    } catch (err) {
      if (isFormValidationError(err)) return
      toast.error(getErrorMessage(err, '建群失败'))
    }
  }, [loadConversations, openConversation])

  const active = conversations.find((c) => c.id === activeId) ?? null
  const totalUnread = conversations.reduce((sum, c) => sum + c.unread, 0)

  return (
    <div className="adm-page adm-page--responsive">
      <PageHeader
        icon={<MessageOutlined />}
        title="站内聊天"
        subtitle="管理员之间的私聊与群聊；消息实时推送，断线自动重连"
        count={{ total: totalUnread }}
        actions={(
          <>
            <Button icon={<PlusOutlined />} onClick={() => setDirectOpen(true)}>
              发起单聊
            </Button>
            <Button icon={<TeamOutlined />} onClick={() => setGroupOpen(true)}>
              建群
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => void loadConversations()}>
              刷新
            </Button>
          </>
        )}
      />

      <div className="chat-layout">
        <aside className={`chat-list${listOpen ? ' chat-list--open' : ''}`}>
          {conversations.length === 0 && (
            <div className="chat-empty">还没有会话，点上方「发起单聊」开始</div>
          )}
          {conversations.map((conv) => (
            <button
              key={conv.id}
              type="button"
              className={`chat-list__item${conv.id === activeId ? ' chat-list__item--active' : ''}`}
              onClick={() => void openConversation(conv.id)}
            >
              <div className="chat-list__head">
                <span className="chat-list__title">
                  {conv.type === 'group' && <TeamOutlined />} {conv.title || `会话 #${conv.id}`}
                </span>
                {conv.unread > 0 && <Tag color="red" style={{ margin: 0 }}>{conv.unread}</Tag>}
              </div>
              <div className="chat-list__preview">
                {conv.last_message
                  ? `${conv.last_message.sender_id === me?.id ? '我' : '对方'}：${conv.last_message.content}`
                  : '（暂无消息）'}
              </div>
              <div className="chat-list__time">{formatTime(conv.updated_at)}</div>
            </button>
          ))}
        </aside>

        <section className="chat-panel">
          {!active && <div className="chat-empty">选择左侧会话开始聊天</div>}
          {active && (
            <>
              <div className="chat-panel__head">
                <span className="chat-panel__title">
                  {active.type === 'group' && <TeamOutlined />} {active.title}
                </span>
                {active.type === 'group' && (
                  <span className="chat-panel__members">
                    成员：{active.profiles.map((p) => p.nickname || p.account || p.id).join('、')}
                  </span>
                )}
              </div>
              <div className="chat-messages" ref={scrollRef}>
                {hasMore && (
                  <Button type="link" size="small" onClick={() => void loadOlder()}>
                    加载更早的消息
                  </Button>
                )}
                {loading && <div className="chat-empty">加载中…</div>}
                {messages.map((m) => (
                  <div
                    key={m.id}
                    className={`chat-bubble chat-bubble--${m.sender_id === me?.id ? 'me' : 'peer'}`}
                  >
                    <div className="chat-bubble__content">{m.content}</div>
                    <div className="chat-bubble__meta">
                      {m.sender_id === me?.id ? '我' : (active.profiles.find((p) => p.id === m.sender_id)?.nickname
                        || active.profiles.find((p) => p.id === m.sender_id)?.account
                        || `用户 ${m.sender_id}`)} · {formatTime(m.created_at)}
                    </div>
                  </div>
                ))}
              </div>
              <div className="chat-composer">
                <Input.TextArea
                  rows={2}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  maxLength={2000}
                  placeholder="输入消息，Enter 发送，Shift+Enter 换行"
                  onPressEnter={(e) => {
                    if (!e.shiftKey) {
                      e.preventDefault()
                      void handleSend()
                    }
                  }}
                />
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  loading={sending}
                  disabled={!input.trim()}
                  onClick={() => void handleSend()}
                >
                  发送
                </Button>
              </div>
            </>
          )}
        </section>
      </div>

      {/* 移动端：左栏收进抽屉 */}
      <Button
        className="chat-list-fab"
        type="primary"
        icon={<MessageOutlined />}
        onClick={() => setListOpen(true)}
      >
        会话{totalUnread > 0 ? ` (${totalUnread})` : ''}
      </Button>
      <Drawer
        title="会话列表"
        open={listOpen}
        onClose={() => setListOpen(false)}
        width={300}
      >
        <div className="chat-list chat-list--in-drawer">
          {conversations.map((conv) => (
            <button
              key={conv.id}
              type="button"
              className={`chat-list__item${conv.id === activeId ? ' chat-list__item--active' : ''}`}
              onClick={() => void openConversation(conv.id)}
            >
              <div className="chat-list__head">
                <span className="chat-list__title">{conv.title}</span>
                {conv.unread > 0 && <Tag color="red" style={{ margin: 0 }}>{conv.unread}</Tag>}
              </div>
              <div className="chat-list__preview">
                {conv.last_message ? conv.last_message.content : '（暂无消息）'}
              </div>
            </button>
          ))}
        </div>
      </Drawer>

      {/* 单聊 / 建群统一走 ModalForm（校验/loading/回填内置，见 ui/modalFormCoverage 守卫） */}
      <ModalForm
        title="发起单聊"
        open={directOpen}
        onCancel={() => setDirectOpen(false)}
        onSubmit={handleOpenDirect}
        okText="发起"
      >
        <Form.Item
          name="user_id"
          label="对方的管理员用户 ID"
          rules={[{ required: true, message: '请输入用户 ID' }]}
        >
          <InputNumber min={1} style={{ width: '100%' }} placeholder="在「用户管理」列表可见" />
        </Form.Item>
      </ModalForm>

      <ModalForm
        title="创建群聊"
        open={groupOpen}
        onCancel={() => setGroupOpen(false)}
        onSubmit={handleCreateGroup}
        okText="创建"
      >
        <Form.Item
          name="title"
          label="群聊标题"
          rules={[{ required: true, message: '请输入群聊标题' }]}
        >
          <Input maxLength={100} placeholder="如：值班沟通" />
        </Form.Item>
        <Form.Item
          name="member_ids"
          label="成员 ID（不含自己，逗号分隔，至少 2 人）"
          rules={[{ required: true, message: '请输入成员 ID' }]}
        >
          <Input placeholder="2, 3, 5" />
        </Form.Item>
      </ModalForm>
    </div>
  )
}
