import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import AiWritingDrawer from './AiWritingDrawer'
import type { WritingOptions } from './types'
import type { WritingTurn } from './useAiWriting'

const options: WritingOptions = {
  enabled: true,
  operations: [
    { operation: 'expand', display_name: '扩写', output_type: 'markdown' },
  ],
  text_models: [{ code: 'qwen', display_name: '通义千问' }],
  image_models: [{ code: 'wanx', display_name: '万相', is_default: true }],
  image_model: null,
  styles: [{ value: 'flat', label: '扁平' }],
  sizes: ['1280*720'],
  image_daily_limit: 30,
  image_used_today: 0,
  hourly_limit: 60,
}

function renderDrawer(props: Partial<Parameters<typeof AiWritingDrawer>[0]> = {}) {
  const onRun = vi.fn()
  const onFillResult = vi.fn()
  render(
    <AiWritingDrawer
      open
      onClose={() => {}}
      options={options}
      turns={[]}
      busy={false}
      onRun={onRun}
      onCancel={() => {}}
      onApply={() => {}}
      onRegenerate={() => {}}
      onFillResult={onFillResult}
      onInsertImage={() => {}}
      {...props}
    />,
  )
  return { onRun, onFillResult }
}

describe('AiWritingDrawer', () => {
  it('补充要求与目标字数随操作一起提交', async () => {
    const user = userEvent.setup()
    const { onRun } = renderDrawer()

    await user.type(screen.getByLabelText('补充要求'), '  更口语化  ')
    await user.type(screen.getByLabelText('目标字数'), '300')
    await user.click(screen.getByRole('button', { name: /扩\s*写/ }))

    expect(onRun).toHaveBeenCalledWith('expand', {
      instruction: '更口语化',
      targetLength: 300,
    })
  })

  it('选择模型后随文本操作提交', async () => {
    const user = userEvent.setup()
    const { onRun } = renderDrawer()

    await user.click(screen.getByRole('combobox', { name: '文本模型' }))
    await user.click(await screen.findByText('通义千问（qwen）'))
    await user.click(screen.getByRole('button', { name: /扩\s*写/ }))

    expect(onRun).toHaveBeenCalledWith('expand', expect.objectContaining({ modelCode: 'qwen' }))
  })

  it('选择插图模型后随生成请求提交', async () => {
    const user = userEvent.setup()
    const { onRun } = renderDrawer()

    expect(screen.queryByLabelText('配图提示词')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '展开配图' }))
    await user.type(screen.getByLabelText('配图提示词'), '一只猫')
    await user.click(screen.getByRole('combobox', { name: '插图模型' }))
    await user.click(await screen.findByText('万相（wanx） · 默认'))
    await user.click(screen.getByRole('button', { name: '生成插图' }))

    expect(onRun).toHaveBeenCalledWith('illustrate', expect.objectContaining({
      image: expect.objectContaining({ model_code: 'wanx', prompt: '一只猫' }),
    }))
  })

  it('失败回合只在状态里标记，错误依赖 toast 不常驻面板', () => {
    const turn: WritingTurn = {
      id: 't-err',
      operation: 'illustrate',
      request: { operation: 'illustrate', content_type: 'node' },
      output: '',
      result: null,
      images: [],
      status: '失败',
      error: '通义万相未配置 API Key，请联系管理员',
      running: false,
    }
    renderDrawer({ turns: [turn] })
    expect(screen.getByText('失败')).toBeInTheDocument()
    expect(screen.queryByText('通义万相未配置 API Key，请联系管理员')).not.toBeInTheDocument()
  })

  it('历史回合默认折叠，最新回合展开，可手动展开历史', async () => {
    const user = userEvent.setup()
    const mk = (id: string, output: string): WritingTurn => ({
      id,
      operation: 'rewrite',
      request: { operation: 'rewrite', content_type: 'node' },
      output,
      result: null,
      images: [],
      status: '已完成',
      error: '',
      running: false,
    })
    renderDrawer({ turns: [mk('old', '旧结果'), mk('new', '新结果')] })
    const outputs = () => [...document.querySelectorAll('.ai-writing-output')]
      .map((el) => el.textContent || '')

    expect(outputs().some((text) => text.includes('新结果'))).toBe(true)
    expect(outputs().some((text) => text.includes('旧结果'))).toBe(false)

    await user.click(screen.getByRole('button', { name: '展开' }))
    expect(outputs().some((text) => text.includes('旧结果'))).toBe(true)

    await user.click(screen.getAllByRole('button', { name: '收起' })[0])
    expect(outputs().some((text) => text.includes('旧结果'))).toBe(false)
  })

  it('关键词填入使用英文逗号连接', async () => {
    const user = userEvent.setup()
    const turn: WritingTurn = {
      id: 't1',
      operation: 'keywords',
      request: { operation: 'keywords', content_type: 'node' },
      output: '',
      result: { keywords: ['AI 写作', '编辑器'] },
      images: [],
      status: '已完成',
      error: '',
      running: false,
    }
    const { onFillResult } = renderDrawer({ turns: [turn] })

    await user.click(screen.getByRole('button', { name: '填入关键词' }))
    expect(onFillResult).toHaveBeenCalledWith('keywords', 'AI 写作, 编辑器')
  })
})
