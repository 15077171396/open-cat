import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Input, Select, Slider, Tooltip, Typography } from 'antd'
import {
  ClearOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons'
import PageHeader from '../../components/ui/PageHeader'
import { toast } from '../../utils/toast'
import { useI18n } from '../../i18n'
import {
  fetchPlaygroundOptions,
  streamPlayground,
  type PlaygroundEvent,
  type PlaygroundModelOption,
  type PlaygroundOptions,
  type PlaygroundTurn,
} from './api'

interface ChatMessage extends PlaygroundTurn {
  /** 本轮回复的用量摘要（usage 事件到达后挂在最后一条 assistant 消息上） */
  usage?: string
  /** 流式回复的失败标记：展示错误而不是静默半截 */
  failed?: boolean
}

/** 单条消息长度上限（与后端 MAX_MESSAGE_CHARS 一致；前端先挡，省一次打回） */
const MAX_MESSAGE_CHARS = 8000

/**
 * AI 对话调试台：模型管理页配完 Key/单价后，当场对话验证连通性与计费。
 * 会话只存在前端（刷新即清空）—— 调试台不是聊天室，服务端不留痕。
 */
export default function AiPlaygroundPage() {
  const { t } = useI18n()
  const [options, setOptions] = useState<PlaygroundOptions | null>(null)
  const [modelCode, setModelCode] = useState<string>('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [temperature, setTemperature] = useState(0.7)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void (async () => {
      try {
        const data = await fetchPlaygroundOptions()
        setOptions(data)
        const defaultItem = data.items.find((item) => item.is_enabled && item.is_default)
          ?? data.items.find((item) => item.is_enabled)
        if (defaultItem) setModelCode(defaultItem.code)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err))
      }
    })()
  }, [])

  // 新回复到达时滚到底部：调试长回复时人工拖动会打断阅读。
  // scrollTo?.：jsdom（测试环境）未实现 Element.scrollTo，可选调用兜底
  useEffect(() => {
    const el = listRef.current
    el?.scrollTo?.({ top: el.scrollHeight })
  }, [messages])

  const modelOptions = useMemo<PlaygroundModelOption[]>(
    () => (options?.items ?? []).filter((item) => item.is_enabled),
    [options],
  )

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text) return
    if (text.length > MAX_MESSAGE_CHARS) {
      toast.error(t('aiPlayground.tooLong'))
      return
    }
    if (busy) return

    const history = [...messages, { role: 'user' as const, content: text }]
    setMessages([...history, { role: 'assistant', content: '' }])
    setInput('')
    setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller

    try {
      await streamPlayground(
        {
          model_code: modelCode || undefined,
          system_prompt: systemPrompt.trim() || undefined,
          messages: history,
          temperature,
        },
        (event: PlaygroundEvent) => {
          if (event.event === 'delta') {
            const chunk = typeof event.data.text === 'string' ? event.data.text : ''
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant') last.content += chunk
              return next
            })
          } else if (event.event === 'usage') {
            const promptTokens = Number(event.data.prompt_tokens ?? 0)
            const completionTokens = Number(event.data.completion_tokens ?? 0)
            const cost = event.data.cost
            const parts = [
              `${promptTokens} + ${completionTokens} ${t('aiPlayground.tokens')}`,
            ]
            if (typeof cost === 'number') parts.push(`≈¥${cost.toFixed(4)}`)
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant') last.usage = parts.join(' · ')
              return next
            })
          }
        },
        controller.signal,
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      if (controller.signal.aborted) {
        // 主动停止：保留已生成的半截内容并打上标记，让「停下来的回复」可辨认
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant') last.failed = true
          return next
        })
      } else {
        toast.error(message)
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant' && !last.content) next.pop()
          return next
        })
      }
    } finally {
      setBusy(false)
      abortRef.current = null
    }
  }, [input, messages, busy, modelCode, systemPrompt, temperature, t])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const clear = useCallback(() => {
    if (busy) abortRef.current?.abort()
    setMessages([])
  }, [busy])

  return (
    <div className="adm-page">
      <PageHeader
        icon="🤖"
        title={t('aiPlayground.title')}
        subtitle={t('aiPlayground.subtitle')}
        actions={(
          <Button icon={<ClearOutlined />} disabled={!messages.length} onClick={clear}>
            {t('aiPlayground.clear')}
          </Button>
        )}
      />

      <div className="ai-playground">
        <aside className="ai-playground__config">
          <Typography.Text strong>{t('aiPlayground.model')}</Typography.Text>
          <Select
            style={{ width: '100%' }}
            value={modelCode || undefined}
            placeholder={t('aiPlayground.modelPlaceholder')}
            onChange={setModelCode}
            options={modelOptions.map((item) => ({
              value: item.code,
              label: `${item.display_name}（${item.provider_label}）`,
            }))}
            notFoundContent={t('aiPlayground.noModels')}
          />

          <Typography.Text strong>{t('aiPlayground.systemPrompt')}</Typography.Text>
          <Input.TextArea
            rows={4}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder={t('aiPlayground.systemPromptPlaceholder')}
            maxLength={2000}
          />

          <Typography.Text strong>
            {t('aiPlayground.temperature')}: {temperature.toFixed(1)}
          </Typography.Text>
          <Slider
            min={0}
            max={2}
            step={0.1}
            value={temperature}
            onChange={setTemperature}
            tooltip={{ open: false }}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('aiPlayground.sessionHint')}
          </Typography.Text>
        </aside>

        <section className="ai-playground__chat">
          <div className="ai-playground__messages" ref={listRef}>
            {messages.length === 0 && (
              <div className="ai-playground__empty">{t('aiPlayground.empty')}</div>
            )}
            {messages.map((msg, index) => (
              <div key={index} className={`ai-playground__bubble ai-playground__bubble--${msg.role}`}>
                <div className="ai-playground__content">
                  {msg.content || (msg.role === 'assistant' && busy ? '…' : '')}
                  {msg.failed && (
                    <Typography.Text type="warning" style={{ display: 'block', marginTop: 4 }}>
                      {t('aiPlayground.stopped')}
                    </Typography.Text>
                  )}
                  {msg.usage && (
                    <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                      {msg.usage}
                    </Typography.Text>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="ai-playground__composer">
            <Input.TextArea
              rows={3}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t('aiPlayground.inputPlaceholder')}
              maxLength={MAX_MESSAGE_CHARS}
              onPressEnter={(e) => {
                // Shift+Enter 换行；裸 Enter 发送（聊天软件惯例）
                if (!e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
              disabled={busy}
            />
            {busy ? (
              <Tooltip title={t('aiPlayground.stop')}>
                <Button danger icon={<StopOutlined />} onClick={stop}>
                  {t('aiPlayground.stop')}
                </Button>
              </Tooltip>
            ) : (
              <Button
                type="primary"
                icon={<SendOutlined />}
                disabled={!input.trim()}
                onClick={() => void send()}
              >
                {t('aiPlayground.send')}
              </Button>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
