/** 正文预览弹窗：内容为当前管理员正在编辑且已过 DOMPurify 的正文 HTML */

import { Modal } from 'antd'

interface PreviewModalProps {
  open: boolean
  /** 已消毒的正文 HTML */
  html: string
  onClose: () => void
}

export default function PreviewModal({ open, html, onClose }: PreviewModalProps) {
  return (
<Modal
        title="预览"
        open={open}
        onCancel={onClose}
        footer={null}
        width={860}
        styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
      >
        {/* 内容来自当前管理员正在编辑的正文，且已过 DOMPurify；
            真正的 XSS 边界在前台 RichText 的更严格白名单 */}
        <div className="quill-preview-body" dangerouslySetInnerHTML={{ __html: html }} />
        <div className="quill-preview-note">
          代码块在前台会按所选语言做语法高亮（预览中显示为纯文本）；正文图片在
          前台会自动走后端代理与 WebP 压缩。
        </div>
      </Modal>
  )
}
