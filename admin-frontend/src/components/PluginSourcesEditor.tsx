import { Select } from 'antd'
import { SOURCE_NAMES, EMPTY_PLUGIN_SOURCE } from '../constants'
import LinkRowsEditor from './LinkRowsEditor'
import type { PluginSource } from '../types'

interface PluginSourcesEditorProps {
  sources: PluginSource[]
  onChange: (sources: PluginSource[]) => void
}

/**
 * 扩展获取来源编辑器：行骨架复用 LinkRowsEditor（url 失焦自动补全 https:// 协议，
 * 与 NetdiskLinksEditor 行为一致），仅自定义来源平台选择列。
 */
export default function PluginSourcesEditor({ sources, onChange }: PluginSourcesEditorProps) {
  return (
    <LinkRowsEditor<PluginSource>
      title="获取来源"
      addText="添加来源"
      emptyText="暂无获取来源，点击右上角「添加来源」"
      items={sources}
      onChange={onChange}
      emptyItem={EMPTY_PLUGIN_SOURCE}
      renderFirstField={(source, update) => (
        <Select
          value={source.source_name}
          onChange={(v) => update((row) => ({ ...row, source_name: v }))}
          options={SOURCE_NAMES.map((p) => ({ value: p, label: p }))}
          style={{ width: 110, flexShrink: 0 }}
        />
      )}
    />
  )
}
