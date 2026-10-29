/** 快捷键与功能说明弹窗：内容完全由常量驱动 */

import { Modal } from 'antd'

import { CODE_BLOCK_LANGUAGES } from './toolbarConfig'
import { SHORTCUT_GROUPS, VIDEO_HINT } from './quillUtils'

interface ShortcutHelpModalProps {
  open: boolean
  onClose: () => void
}

export default function ShortcutHelpModal({ open, onClose }: ShortcutHelpModalProps) {
  return (
<Modal
        title="快捷键与功能说明"
        open={open}
        onCancel={onClose}
        footer={null}
        width={620}
        styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
      >
        {SHORTCUT_GROUPS.map((group) => (
          <div className="quill-shortcut-group" key={group.title}>
            <h4>{group.title}</h4>
            {group.items.map(([key, desc]) => (
              <div className="quill-shortcut-row" key={key}>
                <span className="quill-shortcut-key">{key}</span>
                <span>{desc}</span>
              </div>
            ))}
          </div>
        ))}
        <div className="quill-shortcut-group">
          <h4>内容块</h4>
          <div className="quill-shortcut-row">
            <span className="quill-shortcut-key">提示框</span>
            <span>工具栏下拉选择「信息提示 / 注意事项 / 成功示例 / 风险警告」，再次选择当前类型即取消</span>
          </div>
          <div className="quill-shortcut-row">
            <span className="quill-shortcut-key">任务清单</span>
            <span>列表分组最右侧按钮，可勾选（前台同样渲染为方框清单）</span>
          </div>
          <div className="quill-shortcut-row">
            <span className="quill-shortcut-key">视频</span>
            <span>{VIDEO_HINT}</span>
          </div>
          <div className="quill-shortcut-row">
            <span className="quill-shortcut-key">附件</span>
            <span>上传到对象存储后插入下载卡片，前台展示为可点击的文件条</span>
          </div>
          <div className="quill-shortcut-row">
            <span className="quill-shortcut-key">图片</span>
            <span>点击正文图片可调整尺寸、左右居中、替换图片、填写 Alt 文本</span>
          </div>
          <div className="quill-shortcut-row">
            <span className="quill-shortcut-key">代码块</span>
            <span>
              用「代码块」按钮开启后，右侧下拉选择语言（共 {CODE_BLOCK_LANGUAGES.length} 种）。
              前台只对<b>已选定语言</b>的代码块做语法高亮——不选则按纯文本展示，
              避免自动识别认错语言反而误导读者。
            </span>
          </div>
        </div>
      </Modal>
  )
}
