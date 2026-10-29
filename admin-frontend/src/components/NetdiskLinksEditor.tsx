import { AutoComplete } from 'antd'
import { NETDISK_PLATFORM_OPTIONS, EMPTY_NETDISK_LINK } from '../constants'
import LinkRowsEditor from './LinkRowsEditor'
import type { NetdiskLink } from '../types'

interface NetdiskLinksEditorProps {
  links: NetdiskLink[]
  onChange: (links: NetdiskLink[]) => void
}

/** 平台输入文本 -> 存库值：命中预置中文名则转为英文 key，否则按自定义平台原文存储 */
const toPlatformValue = (text: string): string => {
  const trimmed = text.trim()
  const preset = NETDISK_PLATFORM_OPTIONS.find((o) => o.label === trimmed)
  return preset ? preset.value : trimmed
}

/** 存库值 -> 输入框展示文本：预置 key 显示中文名，自定义平台原样展示 */
const toPlatformDisplay = (platform: string): string => {
  const preset = NETDISK_PLATFORM_OPTIONS.find((o) => o.value === platform)
  return preset ? preset.label : platform
}

/** 网盘链接编辑器（共享组件）：行骨架复用 LinkRowsEditor，仅自定义平台选择列 */
export default function NetdiskLinksEditor({ links, onChange }: NetdiskLinksEditorProps) {
  return (
    <LinkRowsEditor<NetdiskLink>
      title="下载链接"
      addText="添加链接"
      emptyText="暂无下载链接，点击右上角「添加链接」"
      items={links}
      onChange={onChange}
      emptyItem={EMPTY_NETDISK_LINK}
      renderFirstField={(link, update) => (
        <AutoComplete
          value={toPlatformDisplay(link.platform)}
          onChange={(v) => update((row) => ({ ...row, platform: toPlatformValue(v) }))}
          options={[...NETDISK_PLATFORM_OPTIONS]}
          filterOption={(input, option) =>
            (option?.value ?? '').toLowerCase().includes(input.toLowerCase()) ||
            (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
          }
          placeholder="平台，可自定义输入"
          style={{ width: 130, flexShrink: 0 }}
        />
      )}
    />
  )
}
