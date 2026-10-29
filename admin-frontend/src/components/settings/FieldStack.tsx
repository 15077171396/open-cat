import { Space } from 'antd'
import { SettingField } from './SettingField'
import type { SettingDef } from './types'

interface FieldStackProps {
  items: SettingDef[]
  draft: Record<string, string>
  setValue: (key: string, value: string) => void
}

/**
 * 一组设置项（纵向排列，间距 24）。
 *
 * 抽出来的原因：登录管理 / 支付与渠道 / 邮件通知三个页面各写了一份完全相同的实现
 * （同样的 `Space orientation="vertical" size={24}` + `SettingField` 映射）。
 * 三份复制不会立刻出问题，但「间距改了只改一处」「字段渲染多一层包装」这类改动
 * 一定会漏 —— 而漏掉的表现只是某个页面看起来略挤，没人会去查。
 */
export default function FieldStack({ items, draft, setValue }: FieldStackProps) {
  return (
    <Space orientation="vertical" size={24} style={{ width: '100%' }}>
      {items.map((d) => (
        <SettingField
          key={d.key}
          def={d}
          value={draft[d.key] ?? ''}
          onChange={(v) => setValue(d.key, v)}
        />
      ))}
    </Space>
  )
}
