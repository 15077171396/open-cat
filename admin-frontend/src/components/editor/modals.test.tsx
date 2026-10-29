/** 预览 / 快捷键说明弹窗的回归测试：纯 props 驱动组件可在 jsdom 真实渲染断言 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import PreviewModal from './PreviewModal'
import ShortcutHelpModal from './ShortcutHelpModal'
import { CODE_BLOCK_LANGUAGES } from './toolbarConfig'
import { SHORTCUT_GROUPS, VIDEO_HINT } from './quillUtils'

describe('PreviewModal', () => {
  it('open 时渲染传入的 HTML（作为已消毒内容直接注入）', () => {
    render(<PreviewModal open html="<p>内容A<b>加粗B</b></p>" onClose={() => {}} />)
    // antd Modal 走 portal 渲染到 document.body，故按类名从 document 取正文容器
    const body = document.querySelector('.quill-preview-body')
    expect(body).not.toBeNull()
    expect(body?.innerHTML).toContain('<b>加粗B</b>')
    void screen
  })

  it('展示「预览与前台差异」的说明（否则作者会以为高亮没生效）', () => {
    render(<PreviewModal open html="<p>x</p>" onClose={() => {}} />)
    expect(screen.getByText(/语法高亮/)).toBeInTheDocument()
  })

  it('open=false 时不渲染标题内容', () => {
    render(<PreviewModal open={false} html="<p>x</p>" onClose={() => {}} />)
    expect(screen.queryByText(/代码块在前台/)).not.toBeInTheDocument()
  })
})

describe('ShortcutHelpModal', () => {
  it('渲染全部分组及其条目', () => {
    render(<ShortcutHelpModal open onClose={() => {}} />)
    for (const group of SHORTCUT_GROUPS) {
      expect(screen.getByText(group.title)).toBeInTheDocument()
    }
  })

  it('渲染「内容块」说明，且视频提示与常量一致', () => {
    render(<ShortcutHelpModal open onClose={() => {}} />)
    expect(screen.getByText('内容块')).toBeInTheDocument()
    expect(screen.getByText(VIDEO_HINT)).toBeInTheDocument()
  })

  it('代码块说明里的语言数量取自 CODE_BLOCK_LANGUAGES（不写死数字）', () => {
    render(<ShortcutHelpModal open onClose={() => {}} />)
    expect(screen.getByText(new RegExp(`共 ${CODE_BLOCK_LANGUAGES.length} 种`))).toBeInTheDocument()
  })

  it('open=false 时不渲染内容', () => {
    render(<ShortcutHelpModal open={false} onClose={() => {}} />)
    expect(screen.queryByText('内容块')).not.toBeInTheDocument()
  })

  it('关闭按钮回调 onClose', async () => {
    const onClose = vi.fn()
    render(<ShortcutHelpModal open onClose={onClose} />)
    // antd Modal 的关闭按钮有 aria-label="Close"
    const btn = screen.getByRole('button', { name: /close/i })
    btn.click()
    expect(onClose).toHaveBeenCalled()
  })
})
