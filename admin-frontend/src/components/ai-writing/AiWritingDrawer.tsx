import { Alert, Button, Drawer, Input, InputNumber, Segmented, Select } from 'antd'
import { useState } from 'react'
import { markdownToSafeHtml } from '../editor/markdown'
import type { AiFillField, WritingOperation, WritingOptions, WritingRunOptions } from './types'
import type { WritingTurn } from './useAiWriting'

const QUICK_OPERATIONS = new Set<WritingOperation>([
  'rewrite', 'polish', 'expand', 'shorten', 'continue',
])

const OPERATION_LABELS: Record<WritingOperation, string> = {
  rewrite: '改写',
  polish: '润色',
  expand: '扩写',
  shorten: '缩写',
  continue: '续写',
  summarize: '全文摘要',
  seo_description: 'SEO 描述',
  titles: '标题候选',
  keywords: '关键词',
  illustrate_prompt: '配图提示词',
  illustrate: 'AI 配图',
}

function statusTone(turn: WritingTurn): 'running' | 'success' | 'error' | 'muted' {
  if (turn.running || turn.status === '生成中') return 'running'
  if (turn.status === '失败') return 'error'
  if (turn.status === '已取消') return 'muted'
  return 'success'
}

export default function AiWritingDrawer({
  open, onClose, options, turns, busy, onRun, onCancel, onApply, onRegenerate, onFillResult,
  onInsertImage,
}: {
  open: boolean
  onClose: () => void
  options: WritingOptions | null
  turns: WritingTurn[]
  busy: boolean
  onRun: (operation: WritingOperation, options?: WritingRunOptions) => void
  onCancel: () => void
  onApply: (id: string, mode: 'replace' | 'insert') => void
  onRegenerate: (id: string) => void
  onFillResult: (field: AiFillField, value: string) => void
  onInsertImage: (url: string, alt: string) => void
}) {
  const [prompt, setPrompt] = useState('')
  const [alt, setAlt] = useState('')
  const [style, setStyle] = useState<'flat'|'photo'|'render'|'line'>('flat')
  const [size, setSize] = useState<'1024*1024'|'1280*720'|'720*1280'|'1024*768'>('1280*720')
  const [count, setCount] = useState(1)
  const [imageModelCode, setImageModelCode] = useState<string | undefined>(undefined)
  const [imageOpen, setImageOpen] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [targetLength, setTargetLength] = useState<number | null>(null)
  const [modelCode, setModelCode] = useState<string | undefined>(undefined)
  const imageModels = options?.image_models ?? []
  const selectedImageModel = imageModels.find((model) => model.code === imageModelCode)
    ?? imageModels.find((model) => model.is_default)
    ?? imageModels[0]
    ?? options?.image_model
  const imageSummary = selectedImageModel
    ? `${selectedImageModel.display_name} · ${size.replace('*', '×')} · ${count} 张`
    : '未配置图像模型'
  const quickOperations = (options?.operations ?? []).filter((item) => QUICK_OPERATIONS.has(item.operation))
  const fullOperations = (options?.operations ?? []).filter((item) => !QUICK_OPERATIONS.has(item.operation))
  // 历史回合默认折叠：只让最新一轮自动展开，避免多次生成后面板被旧结果淹没。
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set())
  const latestTurnId = turns.length ? turns[turns.length - 1].id : null
  const isTurnExpanded = (turn: WritingTurn) =>
    expandedIds.has(turn.id)
    || (turn.id === latestTurnId && !collapsedIds.has(turn.id))
  const toggleTurn = (turn: WritingTurn) => {
    const expanded = isTurnExpanded(turn)
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (expanded) next.delete(turn.id)
      else next.add(turn.id)
      return next
    })
    setCollapsedIds((prev) => {
      const next = new Set(prev)
      if (expanded) next.add(turn.id)
      else next.delete(turn.id)
      return next
    })
  }
  const turnPreview = (turn: WritingTurn) => {
    const text = turn.output || (turn.result ? JSON.stringify(turn.result) : '')
      || (turn.images.length ? `已生成 ${turn.images.length} 张图片` : '')
    return text.replace(/\s+/g, ' ').slice(0, 80)
  }
  /** 文本操作的补充参数：配图请求不消费它们 */
  const textOptions = (): WritingRunOptions => ({
    instruction: instruction.trim() || undefined,
    targetLength: targetLength ?? undefined,
    ...(modelCode ? { modelCode } : {}),
  })
  // z-index 10000：高于全屏编辑器 .quill-fullscreen 的 9999；mask 关闭，
  // 避免遮罩盖住全屏编辑器与 antd message（约 2010），面板本身有意保持非模态。
  return (
    <Drawer
      title={(
        <div className="ai-writing-head">
          <span className="ai-writing-head__mark" aria-hidden>✦</span>
          <div>
            <div className="ai-writing-head__title">AI 写作</div>
            <div className="ai-writing-head__sub">选中内容后处理，结果手动采纳</div>
          </div>
        </div>
      )}
      size={460}
      rootClassName="ai-writing-drawer"
      open={open}
      onClose={onClose}
      destroyOnHidden={false}
      rootStyle={{ zIndex: 10000 }}
      mask={false}
    >
      <div className="ai-writing-shell">
        {!options?.enabled ? (
          <Alert type="warning" showIcon title="AI 写作未开启" className="ai-writing-disabled" />
        ) : null}

        <section className="ai-writing-compose" aria-label="AI 写作操作">
          <div className="ai-writing-model-row">
            <Select
              allowClear
              value={modelCode}
              onChange={(value) => setModelCode(value || undefined)}
              placeholder="文本模型 · 跟随提示词配置"
              aria-label="文本模型"
              className="ai-writing-model-select"
              // 抽屉根层级是 10000（高于全屏编辑器），Select 默认弹层 1050 会被抽屉正文压住
              styles={{ popup: { root: { zIndex: 10001 } } }}
              options={(options?.text_models || []).map((model) => ({
                value: model.code,
                label: `${model.display_name}（${model.code}）`,
              }))}
            />
            <InputNumber
              min={50}
              max={5000}
              step={50}
              value={targetLength}
              onChange={(value) => setTargetLength(typeof value === 'number' ? value : null)}
              aria-label="目标字数"
              placeholder="目标字数"
              suffix="字"
              className="ai-writing-target"
            />
          </div>
          <Input.TextArea
            rows={2}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            maxLength={500}
            aria-label="补充要求"
            placeholder="补充要求（可选），如：更口语化、面向新手"
            className="ai-writing-instruction"
          />
          <div className="ai-writing-action-block">
            {quickOperations.length ? (
              <div className="ai-writing-action-group">
                <span className="ai-writing-group-label">段落处理</span>
                <div className="ai-writing-actions">
                  {quickOperations.map((item) => (
                    <Button
                      key={item.operation}
                      size="small"
                      disabled={busy}
                      className="ai-writing-action ai-writing-action--primary"
                      onClick={() => onRun(item.operation, textOptions())}
                    >{item.display_name}</Button>
                  ))}
                </div>
              </div>
            ) : null}
            {fullOperations.length ? (
              <div className="ai-writing-action-group">
                <span className="ai-writing-group-label">全文工具</span>
                <div className="ai-writing-actions">
                  {fullOperations.map((item) => (
                    <Button
                      key={item.operation}
                      size="small"
                      type="text"
                      disabled={busy}
                      className="ai-writing-action"
                      onClick={() => onRun(item.operation, textOptions())}
                    >{item.display_name}</Button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </section>

        <section className={`ai-writing-image${imageOpen ? ' is-open' : ''}`}>
          <button
            type="button"
            className="ai-writing-image__toggle"
            aria-expanded={imageOpen}
            aria-label={imageOpen ? '收起配图' : '展开配图'}
            onClick={() => setImageOpen((value) => !value)}
          >
            <span className="ai-writing-image__icon" aria-hidden>✦</span>
            <span className="ai-writing-image__title">AI 配图</span>
            <span className="ai-writing-image__summary">{imageSummary}</span>
            <span className="ai-writing-image__chevron" aria-hidden>{imageOpen ? '−' : '+'}</span>
          </button>
          {imageOpen ? (
            <div className="ai-writing-image__body">
              {!imageModels.length ? (
                <div className="ai-writing-hint">尚未配置图像模型，请先到「模型管理 → 图像模型」添加。</div>
              ) : null}
              <Select
                allowClear
                value={imageModelCode}
                onChange={(value) => setImageModelCode(value || undefined)}
                placeholder="插图模型 · 默认图像模型"
                aria-label="插图模型"
                className="ai-writing-image__model"
                // 同文本模型：抽屉根层级 10000，弹层默认 1050 会被正文压住
                styles={{ popup: { root: { zIndex: 10001 } } }}
                disabled={!imageModels.length}
                options={imageModels.map((model) => ({
                  value: model.code,
                  label: `${model.display_name}（${model.code}）${model.is_default ? ' · 默认' : ''}`,
                }))}
              />
              <Input.TextArea
                rows={2}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                aria-label="配图提示词"
                placeholder="配图提示词（可先用「配图提示词」生成）"
                className="ai-writing-prompt"
              />
              <Input
                value={alt}
                onChange={(e) => setAlt(e.target.value)}
                aria-label="Alt 文本"
                placeholder="Alt 文本（可选）"
              />
              <div className="ai-writing-image__options">
                <Segmented
                  value={style}
                  onChange={(v) => setStyle(v as typeof style)}
                  options={options?.styles.map((s) => ({ value: s.value, label: s.label })) || []}
                  className="ai-writing-seg"
                />
                <Segmented
                  value={size}
                  onChange={(v) => setSize(v as typeof size)}
                  options={(options?.sizes || []).map((s) => ({ value: s, label: s }))}
                  className="ai-writing-seg"
                />
                <Segmented
                  value={count}
                  onChange={(v) => setCount(Number(v))}
                  options={[1, 2, 3, 4]}
                  aria-label="图片数量"
                  className="ai-writing-seg ai-writing-seg--count"
                />
              </div>
              <Button
                type="primary"
                size="small"
                className="ai-writing-generate"
                disabled={busy || !prompt.trim() || !imageModels.length}
                onClick={() => onRun('illustrate', {
                  image: {
                    model_code: imageModelCode || undefined,
                    prompt, alt, style, size, count,
                  },
                })}
              >生成插图</Button>
            </div>
          ) : null}
        </section>

        <section className="ai-writing-history" aria-label="生成记录">
          {turns.length ? (
            <div className="ai-writing-history__head">
              <span>生成记录</span>
              <span className="ai-writing-history__count">{turns.length}</span>
            </div>
          ) : (
            <div className="ai-writing-empty">
              <span className="ai-writing-empty__mark" aria-hidden>✦</span>
              <strong>还没有生成记录</strong>
              <p>选中一段文字，或直接使用上方操作开始。</p>
            </div>
          )}
          <div className="ai-writing-turns">
            {turns.map((turn) => {
              const expanded = isTurnExpanded(turn)
              const preview = turnPreview(turn)
              const tone = statusTone(turn)
              return (
                <div
                  key={turn.id}
                  className={`ai-writing-turn ai-writing-turn--${tone}${expanded ? '' : ' is-collapsed'}`}
                >
                  <div
                    className="ai-writing-turn__header"
                    role="button"
                    tabIndex={0}
                    aria-expanded={expanded}
                    onClick={() => toggleTurn(turn)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        toggleTurn(turn)
                      }
                    }}
                  >
                    <div className="ai-writing-turn__meta">
                      <span className={`ai-writing-dot is-${tone}`} aria-hidden />
                      <span className="ai-writing-turn__name">
                        {OPERATION_LABELS[turn.operation] || turn.operation}
                      </span>
                      <span className="ai-writing-turn__state">{turn.status}</span>
                      {!expanded && preview ? (
                        <span className="ai-writing-turn__preview">{preview}</span>
                      ) : null}
                    </div>
                    <Button
                      type="text"
                      size="small"
                      className="ai-writing-turn__toggle"
                      onClick={(e) => { e.stopPropagation(); toggleTurn(turn) }}
                    >{expanded ? '收起' : '展开'}</Button>
                  </div>
                  {expanded ? (
                    <div className="ai-writing-turn__body">
                      {turn.output ? (
                        <div
                          className="ai-writing-output"
                          dangerouslySetInnerHTML={{ __html: markdownToSafeHtml(turn.output) }}
                        />
                      ) : null}
                      {turn.result ? (
                        <pre className="ai-writing-result">{JSON.stringify(turn.result, null, 2)}</pre>
                      ) : null}
                      {turn.operation === 'illustrate_prompt' && turn.result ? (
                        <Button
                          size="small"
                          className="ai-writing-fill"
                          onClick={() => {
                            setPrompt(String(turn.result?.prompt || ''))
                            setAlt(String(turn.result?.alt || ''))
                            setImageOpen(true)
                          }}
                        >填入配图提示词</Button>
                      ) : null}
                      {turn.images.length ? (
                        <div className="ai-writing-images">
                          {turn.images.map((img) => (
                            <figure key={img.url}>
                              <img src={img.url} alt={img.alt} loading="lazy" />
                              <Button
                                size="small"
                                type="primary"
                                className="ai-writing-image__insert"
                                onClick={() => onInsertImage(img.url, img.alt)}
                              >插入图片</Button>
                            </figure>
                          ))}
                        </div>
                      ) : null}
                      {turn.operation === 'titles' && Array.isArray(turn.result?.titles) ? (
                        <div className="ai-writing-title-options">
                          {(turn.result.titles as string[]).map((title) => (
                            <Button
                              key={title}
                              size="small"
                              className="ai-writing-fill"
                              onClick={() => onFillResult('title', title)}
                            >用标题：{title}</Button>
                          ))}
                        </div>
                      ) : null}
                      {turn.operation === 'keywords' && Array.isArray(turn.result?.keywords) ? (
                        <Button
                          size="small"
                          className="ai-writing-fill"
                          onClick={() => onFillResult('keywords', (turn.result?.keywords as string[]).join(', '))}
                        >填入关键词</Button>
                      ) : null}
                      {turn.operation === 'seo_description' && turn.output ? (
                        <Button
                          size="small"
                          className="ai-writing-fill"
                          onClick={() => onFillResult('seo_description', turn.output)}
                        >填入 SEO 描述</Button>
                      ) : null}
                      {turn.operation === 'summarize' && turn.output ? (
                        <Button
                          size="small"
                          className="ai-writing-fill"
                          onClick={() => onFillResult('summary', turn.output)}
                        >填入简介</Button>
                      ) : null}
                      <div className="ai-writing-turn__actions">
                        {turn.output ? (
                          <Button
                            size="small"
                            type="primary"
                            onClick={() => onApply(turn.id, 'replace')}
                          >替换原文</Button>
                        ) : null}
                        {turn.output ? (
                          <Button size="small" onClick={() => onApply(turn.id, 'insert')}>插入光标</Button>
                        ) : null}
                        {turn.output ? (
                          <Button
                            type="text"
                            size="small"
                            onClick={() => void navigator.clipboard.writeText(turn.output)}
                          >复制</Button>
                        ) : null}
                        {turn.running ? (
                          <Button size="small" danger onClick={onCancel}>取消</Button>
                        ) : (
                          <Button
                            type="text"
                            size="small"
                            className="ai-writing-regenerate"
                            onClick={() => onRegenerate(turn.id)}
                          >重新生成</Button>
                        )}
                      </div>
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        </section>
      </div>
    </Drawer>
  )
}
