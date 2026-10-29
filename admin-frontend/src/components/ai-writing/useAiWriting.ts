import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from '../../utils/toast'
import { streamWriting } from './aiWritingApi'
import type { EditorBridgeApi } from './editorBridge'
import { markdownToSafeHtml } from '../editor/markdown'
import type {
  WritingHistoryTurn,
  WritingOperation,
  WritingRunOptions,
  WritingRunRequest,
  WritingSseEvent,
} from './types'

export interface WritingTurn {
  id: string
  operation: WritingOperation
  request: WritingRunRequest
  output: string
  result: Record<string, unknown> | null
  images: Array<{ url: string; alt: string; width?: number; height?: number }>
  status: string
  error: string
  running: boolean
}

interface Options {
  bridgeRef: { current: EditorBridgeApi | null }
  /** 页面按操作组装上下文：教程页的全文 / 单章取文就靠这个参数区分 */
  buildBaseRequest: (operation: WritingOperation) => Omit<WritingRunRequest, 'operation'>
  stream?: typeof streamWriting
}

/** 没有选中段落就没有意义的操作：宁可提示用户，也不能把空 selection 发给模型 */
const SELECTION_REQUIRED: ReadonlySet<WritingOperation> = new Set([
  'rewrite', 'polish', 'expand', 'shorten',
])

/** 历史只用于面板内多轮调整：最多 3 轮（6 条消息），总字符不超过后端预算 */
const HISTORY_MAX_MESSAGES = 6
const HISTORY_MAX_CHARS = 4000
/** 选区 / 光标前后各带多少字符作为上下文（后端 before/after 上限 10k，2k 足够） */
const CONTEXT_RADIUS = 2000

/** 把一轮已完成的写作折叠成 user/assistant 两条消息；未完成或没有产出的轮次跳过 */
function completedTurnPair(turn: WritingTurn): WritingHistoryTurn[] | null {
  if (turn.running || turn.error || turn.status !== '已完成') return null
  const output = turn.output.trim() || (turn.result ? JSON.stringify(turn.result) : '')
  if (!output) return null
  const ask = turn.request.instruction?.trim() || `执行「${turn.operation}」操作`
  return [
    { role: 'user', content: ask },
    { role: 'assistant', content: output },
  ]
}

/**
 * 组装多轮历史：从最近完成的一轮往前取，最多 3 轮（6 条消息）；
 * 超出 4000 字时丢最旧的整轮，保持 user/assistant 成对出现。
 */
export function buildWritingHistory(turns: WritingTurn[]): WritingHistoryTurn[] {
  const pairs = turns
    .map(completedTurnPair)
    .filter((pair): pair is WritingHistoryTurn[] => pair !== null)
    .slice(-(HISTORY_MAX_MESSAGES / 2))

  const out: WritingHistoryTurn[] = []
  let chars = 0
  for (let i = pairs.length - 1; i >= 0; i -= 1) {
    let pair = pairs[i]
    let size = pair[0].content.length + pair[1].content.length
    if (chars + size > HISTORY_MAX_CHARS) {
      if (out.length > 0) break
      // 最新一轮单独就超预算：保留提问，截断回答；更旧的整轮直接丢弃
      const budget = HISTORY_MAX_CHARS - pair[0].content.length
      if (budget <= 1) break
      pair = [pair[0], { ...pair[1], content: `${pair[1].content.slice(0, budget - 1)}…` }]
      size = pair[0].content.length + pair[1].content.length
    }
    out.unshift(pair[0], pair[1])
    chars += size
  }
  return out
}

export function useAiWriting({ bridgeRef, buildBaseRequest, stream = streamWriting }: Options) {
  const [turns, setTurns] = useState<WritingTurn[]>([])
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // 页面卸载（切换路由/关闭宿主）时中止在途请求：否则连接会继续跑到生成结束，
  // 白烧 token 与小时额度（spec 4.2）。这里只 abort，不写状态——组件已卸载，
  // 收尾的 setState 是无意义的空操作；沿用 cancel() 会在这里触发卸载后更新。
  useEffect(() => () => { abortRef.current?.abort() }, [])

  const patchTurn = useCallback((id: string, patch: Partial<WritingTurn>) => {
    setTurns((prev) => prev.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)))
  }, [])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    setTurns((prev) => prev.map((t) => (t.running ? { ...t, running: false, status: '已取消' } : t)))
  }, [])

  /** 清空面板与全部回合（切章 / 换文档时调用）：避免上一章的结果被应用到新章节 */
  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    setTurns([])
    setOpen(false)
  }, [])

  const run = useCallback(async (operation: WritingOperation, options: WritingRunOptions = {}) => {
    // abortRef 是同步的，比 busy 状态更早挡住双击/同帧重复触发，保证同一时间只有一个流。
    if (busy || abortRef.current) return
    const bridge = bridgeRef.current
    if (!bridge) {
      toast.error('编辑器尚未就绪')
      return
    }
    const selection = bridge.getSelection()
    if (SELECTION_REQUIRED.has(operation) && !selection) {
      toast.error('请先选中要处理的文字')
      return
    }
    const caret = bridge.getCaret()
    // 续写没有落点就不发：空 before 会让模型从头编一段，结果没法落到正文里
    if (operation === 'continue' && caret == null && !selection) {
      toast.error('请先在正文中定位光标后再续写')
      return
    }
    const base = buildBaseRequest(operation)
    const fullText = bridge.getFullText()
    const selectionEnd = selection ? selection.index + selection.length : null
    // 续写以当前光标为准（选区终点 / 折叠光标）；面板抢走焦点后仍保留的旧选区
    // 只能当兜底，拿来当落点会把内容写到上一次选中的段落。
    const anchorStart = operation === 'continue' ? (caret ?? selectionEnd) : selection?.index ?? null
    const anchorEnd = operation === 'continue' ? (caret ?? selectionEnd) : selectionEnd
    const request: WritingRunRequest = {
      ...base,
      operation,
      model_code: options.modelCode || undefined,
      instruction: options.instruction?.trim() || undefined,
      target_length: options.targetLength ?? null,
      selection: operation === 'continue' || operation === 'illustrate' ? null : selection,
      context: {
        ...base.context,
        article_text: base.context?.article_text || fullText,
        before: anchorStart == null ? '' : fullText.slice(Math.max(0, anchorStart - CONTEXT_RADIUS), anchorStart),
        after: anchorEnd == null ? '' : fullText.slice(anchorEnd, anchorEnd + CONTEXT_RADIUS),
        outline: base.context?.outline?.length ? base.context.outline : bridge.getOutline(),
      },
      history: buildWritingHistory(turns),
      image: options.image,
    }
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`
    const turn: WritingTurn = {
      id, operation, request, output: '', result: null, images: [], status: '生成中', error: '', running: true,
    }
    setTurns((prev) => [...prev, turn])
    setOpen(true)
    setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await stream(request, (event: WritingSseEvent) => {
        // 取消后仍可能收到已排队的帧，丢弃即可，避免取消后文本继续变长。
        if (controller.signal.aborted) return
        if (event.event === 'delta') {
          const text = String(event.data.text || '')
          setTurns((prev) => prev.map((t) => t.id === id ? { ...t, output: t.output + text } : t))
        } else if (event.event === 'result') {
          patchTurn(id, { result: event.data })
        } else if (event.event === 'image') {
          setTurns((prev) => prev.map((t) => t.id === id ? {
            ...t, images: [...t.images, {
              url: String(event.data.url || ''), alt: String(event.data.alt || ''),
              width: Number(event.data.width) || undefined, height: Number(event.data.height) || undefined,
            }],
          } : t))
        } else if (event.event === 'status') {
          patchTurn(id, { status: String(event.data.stage || '生成中') })
        }
      }, controller.signal)
      if (!controller.signal.aborted) patchTurn(id, { running: false, status: '已完成' })
    } catch (err) {
      if ((err instanceof DOMException || err instanceof Error) && err.name === 'AbortError') return
      const text = err instanceof Error ? err.message : 'AI 请求失败'
      patchTurn(id, { running: false, status: '失败', error: text })
      toast.error(text)
    } finally {
      // 旧请求收尾时不能把取消后新启动的请求的 controller / busy 清掉。
      if (abortRef.current === controller) {
        abortRef.current = null
        setBusy(false)
      }
    }
  }, [bridgeRef, buildBaseRequest, busy, stream, patchTurn, turns])

  const applyTurn = useCallback((id: string, mode: 'replace' | 'insert') => {
    const turn = turns.find((t) => t.id === id)
    const bridge = bridgeRef.current
    if (!turn || !bridge) return
    const html = markdownToSafeHtml(turn.output)
    if (mode === 'insert') {
      bridge.insertHtml(html)
      return
    }
    // 用发起时的选区做漂移校验（bridge 比对 expectedText）：期间正文可能已变化，
    // 以当前选区的文本自校验会永远通过，等于跳过检查。
    const original = turn.request.selection
    if (original && bridge.replaceRange(original.index, original.length, html, original.text)) return
    toast.warning(original ? '原选区已变化，已改为插入到当前光标' : '没有可替换的原选区，已改为插入到当前光标')
    bridge.insertHtml(html)
  }, [bridgeRef, turns])

  const regenerate = useCallback((id: string) => {
    const turn = turns.find((t) => t.id === id)
    if (!turn) return
    // 表单可能已被改动，按回合保存的请求重放图片 / 补充要求 / 目标字数。
    void run(turn.operation, {
      image: turn.request.image,
      instruction: turn.request.instruction,
      targetLength: turn.request.target_length ?? undefined,
      modelCode: turn.request.model_code,
    })
  }, [run, turns])

  return { turns, busy, open, setOpen, run, cancel, reset, regenerate, applyTurn }
}
