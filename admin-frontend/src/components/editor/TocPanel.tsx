/** 编辑器右侧「目录」面板：纯展示组件，滚动逻辑由父组件实现 */

/** 目录项：标题在正文中的锚点 id、显示文案、层级 */
export interface TocItem {
  id: string
  text: string
  level: number
}

interface TocPanelProps {
  /** 标题列表；为空时展示引导文案 */
  items: TocItem[]
  /** 点击目录项 → 滚动到对应标题（滚动逻辑由父组件实现） */
  onSelect: (id: string) => void
}

export default function TocPanel({ items, onSelect }: TocPanelProps) {
  return (
    <div className="quill-toc-container">
      <div className="quill-toc-title">目录</div>
      {items.length > 0 ? (
        items.map((item) => (
          <button
            key={item.id}
            type="button"
            className="quill-toc-item"
            onClick={() => onSelect(item.id)}
            style={{ paddingLeft: (item.level - 1) * 12 }}
          >
            {item.text}
          </button>
        ))
      ) : (
        <div
          style={{
            color: 'var(--app-text-4)',
            fontSize: 12,
            textAlign: 'center',
            padding: '24px 8px',
            lineHeight: 1.8,
          }}
        >
          添加标题后
          <br />
          自动生成目录
        </div>
      )}
    </div>
  )
}
