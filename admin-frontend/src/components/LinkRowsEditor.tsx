import type { ReactNode } from 'react'
import { Button, Input } from 'antd'
import { PlusOutlined, DeleteOutlined, LinkOutlined } from '@ant-design/icons'

/** 通用链接行：url 与提取码固定由骨架渲染，首列（平台/来源）由调用方自定义 */
export interface LinkRow {
  url: string
  extract_code: string
}

interface LinkRowsEditorProps<T extends LinkRow> {
  /** 区块标题（如 "下载链接" / "获取来源"） */
  title: string
  /** 添加按钮文案 */
  addText: string
  /** 空列表占位文案 */
  emptyText: string
  items: T[]
  onChange: (items: T[]) => void
  /** 新增行时的空白项模板 */
  emptyItem: T
  /** 渲染每行首列（平台/来源选择器），update 接收行级映射函数回写该行 */
  renderFirstField: (item: T, update: (mapper: (row: T) => T) => void) => ReactNode
}

/**
 * 链接行编辑器通用骨架（NetdiskLinksEditor / PluginSourcesEditor 共用）：
 * 标题栏 + 行列表（自定义首列 + 链接输入 + 提取码 + 删除）。
 * 行内 url 失焦时自动补全 https:// 协议，避免无协议链接入库后
 * 前台跳转时被当作相对路径。
 */
export default function LinkRowsEditor<T extends LinkRow>({
  title,
  addText,
  emptyText,
  items,
  onChange,
  emptyItem,
  renderFirstField,
}: LinkRowsEditorProps<T>) {
  /** 以行级映射函数更新指定行（泛型下用函数式 patch，避免 Partial<T> 字面量不可赋值问题） */
  const patchRow = (idx: number, mapper: (row: T) => T) => {
    onChange(items.map((it, i) => (i === idx ? mapper(it) : it)))
  }

  // 失焦时自动补全协议：www.baidu.com / baidu.com -> https://...
  const normalizeUrl = (idx: number, value: string) => {
    const trimmed = value.trim()
    if (!trimmed) return
    if (/^https?:\/\//i.test(trimmed)) return
    patchRow(idx, (row) => ({ ...row, url: `https://${trimmed}` }))
  }

  return (
    <div
      style={{
        border: '1px solid #f0f0f0',
        borderRadius: 10,
        background: '#fafbfc',
        padding: 16,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 12,
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 600, color: '#1f1f1f', display: 'flex', alignItems: 'center', gap: 6 }}>
          <LinkOutlined style={{ color: '#1677ff' }} />
          {title}
        </span>
        <Button type="primary" ghost size="small" icon={<PlusOutlined />} onClick={() => onChange([...items, { ...emptyItem }])}>
          {addText}
        </Button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {items.length === 0 && (
          <div
            style={{
              textAlign: 'center',
              padding: '32px 16px',
              color: '#bfbfbf',
              fontSize: 13,
            }}
          >
            {emptyText}
          </div>
        )}
        {items.map((item, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '10px 12px',
              background: '#fff',
              borderRadius: 8,
              border: '1px solid #f0f0f0',
            }}
          >
            {renderFirstField(item, (mapper) => patchRow(idx, mapper))}
            <Input
              value={item.url}
              onChange={(e) => patchRow(idx, (row) => ({ ...row, url: e.target.value }))}
              onBlur={(e) => normalizeUrl(idx, e.target.value)}
              placeholder="链接地址，如 www.baidu.com"
              style={{ flex: 1, minWidth: 0 }}
            />
            <Input
              value={item.extract_code}
              onChange={(e) => patchRow(idx, (row) => ({ ...row, extract_code: e.target.value }))}
              placeholder="提取码"
              style={{ width: 90, flexShrink: 0 }}
            />
            {items.length > 1 && (
              <Button
                danger
                size="small"
                type="text"
                icon={<DeleteOutlined />}
                onClick={() => onChange(items.filter((_, i) => i !== idx))}
                style={{ flexShrink: 0 }}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
