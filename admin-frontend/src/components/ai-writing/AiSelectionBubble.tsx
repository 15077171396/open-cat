import { Button, Space } from 'antd'
import type { WritingOperation } from './types'

const QUICK_ACTIONS: Array<{ op: WritingOperation; label: string }> = [
  { op: 'rewrite', label: '改写' },
  { op: 'polish', label: '润色' },
  { op: 'expand', label: '扩写' },
  { op: 'shorten', label: '缩写' },
  { op: 'continue', label: '续写' },
]

export default function AiSelectionBubble({
  visible, top, left, onAction, onMore,
}: {
  visible: boolean
  top: number
  left: number
  onAction: (operation: WritingOperation) => void
  onMore: () => void
}) {
  if (!visible) return null
  return (
    <div
      className="ai-selection-bubble"
      style={{ top, left }}
      role="toolbar"
      aria-label="AI 写作操作"
      // 按下浮条时阻止默认行为：mousedown 默认会把焦点从 Quill 移走并清空文档选区，
      // Quill 2 随即派发 selection-change(null)，页面会在按钮 click 之前卸载浮条，
      // 快捷操作便永远发不出去。click 不受 preventDefault 影响，操作仍能触发。
      onMouseDown={(e) => e.preventDefault()}
    >
      <Space size={4} wrap>
        {QUICK_ACTIONS.map((item) => (
          <Button key={item.op} size="small" type="text" onClick={() => onAction(item.op)}>
            {item.label}
          </Button>
        ))}
        <Button size="small" type="text" onClick={onMore}>更多</Button>
      </Space>
    </div>
  )
}
