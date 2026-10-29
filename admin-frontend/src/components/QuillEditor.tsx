import { useCallback, useRef, useState, useEffect, useMemo } from 'react'
import ReactQuill, { Quill } from 'react-quill-new'
import 'react-quill-new/dist/quill.snow.css'
import QuillTableBetter from 'quill-table-better'
import 'quill-table-better/dist/quill-table-better.css'
import Delta from 'quill-delta'
import { Input, Modal } from 'antd'
import { uploadImage, uploadResourceFile } from '../utils/upload'
import {
  IMAGE_ALIGN_VALUES,
  imageAlignClass,
  parseVideoUrl,
  type ImageAlign,
} from './editor/quillBlots'
import { EDITOR_EXTRA_CSS, STATIC_EDITOR_CSS } from './editor/editorStyles'
import PreviewModal from './editor/PreviewModal'
import ShortcutHelpModal from './editor/ShortcutHelpModal'
import TocPanel, { type TocItem } from './editor/TocPanel'
import ImageSearchModal from './image-search/ImageSearchModal'
import { insertImageIntoEditor } from './image-search/insertIntoEditor'
import { getImageSearchOptions } from './image-search/imageSearchApi'
import type { ImageInsertPayload } from './image-search/types'
import {
  TOC_DEBOUNCE_MS,
  DEFAULT_HEIGHT,
  DEFAULT_MIN_HEIGHT,
  IMAGE_RESIZE_MIN_WIDTH,
  sanitizePreviewHtml,
  VIDEO_HINT,
  resolveSize,
  INSERT_PLACEHOLDER,
  locateUploadPlaceholder,
  extractImageFiles,
  formatBytes,
  computeStats,
  collectMatches,
  replaceBase64ImagesWithUrls,
  BASE64_IMG_REGEX,
  type FindMatch,
} from './editor/quillUtils'
import {
  applySelectOptionLabels,
  CUSTOM_ICONS,
  FULLSCREEN_ENTER_SVG,
  FULLSCREEN_EXIT_SVG,
  QUILL_FORMATS,
  QUILL_TOOLBAR_CONTAINER,
  STRUCTURE_KEYBOARD_BINDINGS,
  TOOLBAR_TITLES,
} from './editor/toolbarConfig'
import { createEditorBridge, type EditorBridgeApi } from './ai-writing/editorBridge'

import { toast } from '../utils/toast'
// 注册表格增强模块（模块级执行一次，会同步注册全部表格 blot 与扩展后的 toolbar/clipboard 模块）
Quill.register({ 'modules/table-better': QuillTableBetter }, true)

// 两处第三方兼容性补丁（setContents 重定向 + LeafBlot 兜底），详见模块内注释
import './editor/quillPatches'

// 模块级常量：避免每次渲染重建对象/字符串

/** 静态样式表的全局注入节点与引用计数（多编辑器实例共享一份，最后一个卸载时移除） */
let staticStyleEl: HTMLStyleElement | null = null
let staticStyleRefs = 0

interface QuillEditorProps {
  value?: string
  onChange?: (v: string) => void
  placeholder?: string
  /** 编辑器正文区域高度（CSS 长度值或数值，数值按 px 处理），默认 'calc(100vh - 360px)' */
  height?: string | number
  /** 编辑器正文区域最小高度（CSS 长度值或数值），默认 '400px' */
  minHeight?: string | number
  /** 是否显示右侧目录侧边栏，默认 true */
  showToc?: boolean
  /**
   * 正文随内容自适应高度（不内部滚动），`height` 退化为最小高度。
   *
   * 用途：长文档（如法律文书）需要在**页面滚动**时让工具栏一直吸顶。
   * 固定高度时 `.ql-container` 自己滚动，工具栏的 sticky 行程被容器高度限死
   * （360px 的编辑器只有约 318px 行程，滚过就跟着走）；改成自适应高度后，
   * 工具栏的行程覆盖整篇文档，滚到哪里都在顶部。
   */
  autoHeight?: boolean
  /** 编辑器实例就绪后回调桥接 API（供 AI 写作等外部能力读写正文） */
  onReady?: (api: EditorBridgeApi) => void
  /** 是否展示工具栏的 AI 写作入口，默认 false（入口隐藏时不会触发 onAiOpen） */
  aiEnabled?: boolean
  /** 点击工具栏 AI 写作入口 */
  onAiOpen?: () => void
  /** 是否展示工具栏的图片搜索入口；不传时按后台开关自动判断 */
  imageSearchEnabled?: boolean
  /** 选区是否有内容变化时回调（供 AI 气泡按选区显隐） */
  onSelectionChange?: (hasSelection: boolean) => void
}

/** 富文本编辑器组件：工具栏 + 表格 + 图片上传/调整 + 视频/附件插入 + 查找替换 + 源码模式 + 目录面板 */
export default function QuillEditor({
  value,
  onChange,
  placeholder,
  height,
  minHeight,
  showToc = true,
  autoHeight = false,
  onReady,
  aiEnabled = false,
  onAiOpen,
  imageSearchEnabled,
  onSelectionChange,
}: QuillEditorProps) {
  const quillRef = useRef<ReactQuill>(null)
  // 图片调整大小 overlay 的挂载容器（编辑器外层 wrapper，position:relative）
  const editorWrapperRef = useRef<HTMLDivElement>(null)
  const [toc, setToc] = useState<TocItem[]>([])
  const [isFullscreen, setIsFullscreen] = useState(false)
  // Quill toolbar handler 通过 ref 读取最新状态，避免闭包陈旧值
  const isFullscreenRef = useRef(false)

  // 回调统一经 ref 转发：桥接 effect 与 toolbar handler 只创建一次，
  // 直接闭包会永远拿到首次渲染时的 props。
  const onReadyRef = useRef(onReady)
  useEffect(() => { onReadyRef.current = onReady }, [onReady])
  const onAiOpenRef = useRef(onAiOpen)
  useEffect(() => { onAiOpenRef.current = onAiOpen }, [onAiOpen])
  const onSelectionChangeRef = useRef(onSelectionChange)
  useEffect(() => { onSelectionChangeRef.current = onSelectionChange }, [onSelectionChange])

  // 查找替换
  const [findOpen, setFindOpen] = useState(false)
  const [findText, setFindText] = useState('')
  const [replaceText, setReplaceText] = useState('')
  const [findCaseSensitive, setFindCaseSensitive] = useState(false)
  const [findCount, setFindCount] = useState<number | null>(null)
  const findInputRef = useRef<HTMLInputElement>(null)

  // HTML 源码模式
  const [sourceMode, setSourceMode] = useState(false)
  const [sourceText, setSourceText] = useState('')

  // 弹层
  const [helpOpen, setHelpOpen] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewHtml, setPreviewHtml] = useState('')
  const [videoOpen, setVideoOpen] = useState(false)
  const [videoUrl, setVideoUrl] = useState('')
  const [imageSearchOpen, setImageSearchOpen] = useState(false)
  const [imageSearchQuery, setImageSearchQuery] = useState('')
  const [imageSearchAvailable, setImageSearchAvailable] = useState(imageSearchEnabled ?? false)
  const imageSearchAtRef = useRef(0)

  useEffect(() => {
    if (imageSearchEnabled !== undefined) {
      setImageSearchAvailable(imageSearchEnabled)
      return
    }
    let cancelled = false
    getImageSearchOptions()
      .then((options) => {
        if (cancelled) return
        const hasSource = options.local_available
          || options.sources.some((source) => source.configured)
        setImageSearchAvailable(options.enabled && hasSource)
      })
      .catch(() => {
        if (!cancelled) setImageSearchAvailable(false)
      })
    return () => { cancelled = true }
  }, [imageSearchEnabled])

  // 源码模式状态同样需要给 Quill handler 读取（handler 在 useMemo 中创建，
  // 直接闭包 state 会拿到陈旧值）
  const sourceModeRef = useRef(false)
  useEffect(() => {
    sourceModeRef.current = sourceMode
  }, [sourceMode])

  /**
   * 正文规模统计：computeStats 对整个 HTML 跑 DOMParser + 两次正则，
   * 逐按键同步执行会让输入卡顿，必须防抖；初值同步算一次避免首屏为空。
   */
  const [stats, setStats] = useState(() => computeStats(value || ''))
  useEffect(() => {
    const timer = setTimeout(() => setStats(computeStats(value || '')), TOC_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [value])

  // 静态样式表全局去重注入：多编辑器实例共享同一份 <style>，按引用计数管理生命周期
  useEffect(() => {
    if (staticStyleRefs === 0 && !staticStyleEl) {
      const el = document.createElement('style')
      el.id = 'quill-editor-static-css'
      el.textContent = STATIC_EDITOR_CSS + EDITOR_EXTRA_CSS
      document.head.appendChild(el)
      staticStyleEl = el
    }
    staticStyleRefs++
    return () => {
      staticStyleRefs--
      if (staticStyleRefs === 0 && staticStyleEl) {
        staticStyleEl.remove()
        staticStyleEl = null
      }
    }
  }, [])

  // 选区与插入能力对 AI 面板开放：桥接持有自己的选区缓存，
  // 组件侧的监听只负责把「有无选区」通知给气泡，两者互不依赖。
  useEffect(() => {
    let cancelled = false
    let bridge: EditorBridgeApi | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let offSelection: (() => void) | undefined
    const attach = (): boolean => {
      if (cancelled) return true
      try {
        const editor = quillRef.current?.getEditor()
        if (!editor) return false
        bridge = createEditorBridge(editor)
        const onChange = (range: { length?: number } | null) => {
          onSelectionChangeRef.current?.(!!range && (range.length || 0) > 0)
        }
        editor.on('selection-change', onChange)
        offSelection = () => editor.off('selection-change', onChange)
        onReadyRef.current?.(bridge)
        return true
      } catch {
        return false
      }
    }
    // 编辑器初始化有延迟，首次未就绪则短延迟重试
    if (!attach()) timer = setTimeout(attach, 100)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      offSelection?.()
      bridge?.dispose()
    }
  }, [])

  // 受控 value 程序化同步（表单回填/切章，source='api'）也会触发 onChange；
  // 放任上抛会让 antd Form 误标 touched、未保存守卫误弹，故仅拦截 'api' source
  const handleChange = useCallback(
    (v: string, _delta: unknown, source: string) => {
      if (source === 'api') return
      onChange?.(v)
    },
    [onChange],
  )

  /** 切换全屏 / 退出全屏 */
  const toggleFullscreen = useCallback(() => {
    const next = !isFullscreenRef.current
    isFullscreenRef.current = next
    setIsFullscreen(next)
  }, [])

  /** 上传单张图片：光标处插占位 → 上传 → 删占位插远程 URL（工具栏/粘贴/拖拽三路共用） */
  const insertImageWithUpload = useCallback(async (file: File) => {
    const editor = quillRef.current?.getEditor()
    if (!editor) return
    const range = editor.getSelection(true) || { index: editor.getLength(), length: 0 }

    editor.insertText(range.index, INSERT_PLACEHOLDER, 'user')
    try {
      const { url } = await uploadImage(file)
      // 上传期间作者继续输入会让索引漂移，完成时一律按文本重新定位
      const at = locateUploadPlaceholder(editor.getText())
      if (at >= 0) {
        editor.deleteText(at, INSERT_PLACEHOLDER.length)
        editor.insertEmbed(at, 'image', url, 'user')
        editor.setSelection(at + 1, 0)
      } else {
        // 占位已被作者删/改：插到当前光标处，宁可位置不完美也不动正文
        const cursor = editor.getSelection()?.index ?? editor.getLength() - 1
        editor.insertEmbed(cursor, 'image', url, 'user')
      }
    } catch (err) {
      // 用 'user' source 删除占位符：'api' source 已被 onChange 过滤，默认 source 会让占位残留
      const at = locateUploadPlaceholder(editor.getText())
      if (at >= 0) editor.deleteText(at, INSERT_PLACEHOLDER.length, 'user')
      toast.error(err instanceof Error ? err.message : '图片上传失败')
    }
  }, [])

  /** 串行上传多张图片，保证插入顺序与占位互不覆盖 */
  const insertImagesSequentially = useCallback(
    async (files: File[]) => {
      for (const file of files) await insertImageWithUpload(file)
    },
    [insertImageWithUpload]
  )

  const openImageSearch = useCallback(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    const range = quill.getSelection(true)
      || { index: Math.max(0, quill.getLength() - 1), length: 0 }
    imageSearchAtRef.current = range.index
    const selected = range.length > 0
      ? quill.getText(range.index, range.length).trim()
      : ''
    setImageSearchQuery(selected.slice(0, 40))
    setImageSearchOpen(true)
  }, [])

  const handleImageInsert = useCallback((payload: ImageInsertPayload) => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    insertImageIntoEditor(quill, imageSearchAtRef.current, payload)
    quill.focus()
    toast.success('图片已插入')
  }, [])

  // 工具栏图片按钮：选择文件 → 上传 COS → 插入 URL；handlers 中 this 为 Quill Toolbar 实例
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  /** 复用的隐藏文件选择框：每次新建 input 会在点取消后于 body 累积游离 DOM */
  const pickFile = useCallback(
    (ref: { current: HTMLInputElement | null }, accept: string, onPicked: (file: File) => void) => {
      const existing = ref.current
      if (existing && document.body.contains(existing)) {
        existing.value = '' // 清空以允许连续两次选择同一文件
        existing.click()
        return
      }
      const input = document.createElement('input')
      input.setAttribute('type', 'file')
      input.setAttribute('accept', accept)
      input.style.display = 'none'
      document.body.appendChild(input)
      ref.current = input
      input.onchange = () => {
        const file = input.files?.[0]
        if (file) onPicked(file)
        input.value = ''
      }
      input.click()
    },
    [],
  )

  const handleImageUpload = useCallback(function (this: unknown) {
    pickFile(fileInputRef, 'image/jpeg,image/png,image/webp,image/gif', (file) => {
      void insertImagesSequentially([file])
    })
  }, [pickFile, insertImagesSequentially])

  // 工具栏附件按钮：选择文件 → 上传 COS → 插入下载卡片
  const attachmentInputRef = useRef<HTMLInputElement | null>(null)

  const handleAttachmentUpload = useCallback(function (this: unknown) {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    pickFile(attachmentInputRef, '*/*', async (file) => {
      const hide = toast.loading('附件上传中...', 0)
      try {
        const res = await uploadResourceFile(file)
        const range = quill.getSelection(true) || { index: quill.getLength() - 1, length: 0 }
        quill.insertEmbed(
          range.index,
          'attachment',
          { href: res.url, name: file.name, size: formatBytes(res.size || file.size) },
          'user',
        )
        quill.setSelection(range.index + 1, 0, 'silent')
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '附件上传失败')
      } finally {
        hide()
      }
    })
  }, [pickFile])

  // 组件卸载时移除复用的隐藏 input
  useEffect(() => {
    return () => {
      fileInputRef.current?.remove()
      fileInputRef.current = null
      attachmentInputRef.current?.remove()
      attachmentInputRef.current = null
    }
  }, [])

  // 粘贴 / 拖拽图片拦截（capture 阶段先于 Quill 默认处理）：
  // 不拦截 Quill 会把图片转成 base64 内联进正文，导致正文膨胀到 MB 级
  useEffect(() => {
    let root: HTMLElement | undefined
    let retryTimer: ReturnType<typeof setTimeout> | undefined

    // 将上传函数适配为返回 URL 的形式
    const uploadToUrl = async (file: File): Promise<string> => {
      const { url } = await uploadImage(file)
      return url
    }

    const onPaste = (e: ClipboardEvent) => {
      const images = extractImageFiles(e.clipboardData?.files)
      // 情况 1：直接粘贴截图，files 中有图片文件
      if (images.length > 0) {
        e.preventDefault()
        e.stopPropagation()
        void insertImagesSequentially(images)
        return
      }

      // 情况 2：从网页/Word/其他文章复制带图片内容，图片以 base64 存在于 text/html
      const html = e.clipboardData?.getData('text/html')
      if (html && BASE64_IMG_REGEX.test(html)) {
        e.preventDefault()
        e.stopPropagation()
        // BASE64_IMG_REGEX 带 g 标志，test 后 lastIndex 被修改，需重置
        BASE64_IMG_REGEX.lastIndex = 0
        // 异步上传所有 base64 图片并以远程 URL 替换，完成后将处理后的 HTML 插入编辑器
        void replaceBase64ImagesWithUrls(html, uploadToUrl).then((cleanedHtml) => {
          const editor = quillRef.current?.getEditor()
          if (!editor) return
          const range = editor.getSelection(true) || { index: editor.getLength(), length: 0 }
          // 用 clipboard 模块安全地插入 HTML（会走 Quill 的 Delta 解析，比直接 insertEmbed 更通用）
          const clipboard = editor.getModule('clipboard') as { dangerouslyPasteHTML?: (index: number, html: string, source?: string) => void }
          clipboard.dangerouslyPasteHTML?.(range.index, cleanedHtml, 'user')
        }).catch((err) => {
          console.warn('粘贴内容上传失败:', err)
          toast.error('粘贴图片上传失败，请重试')
        })
      }
      // 情况 3：纯文本或不含图片的 HTML，交给 Quill 默认处理
    }

    const onDrop = (e: DragEvent) => {
      const images = extractImageFiles(e.dataTransfer?.files)
      if (images.length === 0) return
      e.preventDefault()
      e.stopPropagation()
      void insertImagesSequentially(images)
    }

    const attach = (): boolean => {
      try {
        root = quillRef.current?.getEditor()?.root
      } catch {
        return false
      }
      if (!root) return false
      root.addEventListener('paste', onPaste, true)
      root.addEventListener('drop', onDrop, true)
      return true
    }

    // 编辑器初始化有延迟，首次未就绪则短延迟重试
    if (!attach()) {
      retryTimer = setTimeout(attach, 100)
    }
    return () => {
      if (retryTimer) clearTimeout(retryTimer)
      if (root) {
        root.removeEventListener('paste', onPaste, true)
        root.removeEventListener('drop', onDrop, true)
      }
    }
  }, [insertImagesSequentially])

  // 图片拖拽调整大小：点击图片显示选框与手柄，拖拽等比缩放后以 width 属性写入
  // image blot（前台白名单允许 width 属性）；同一面板承载对齐、替换与 Alt 文本
  useEffect(() => {
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const cleanups: Array<() => void> = []

    const attach = (): boolean => {
      if (cancelled) return true
      let editor: Quill | undefined
      try {
        editor = quillRef.current?.getEditor()
      } catch {
        return false
      }
      const wrapper = editorWrapperRef.current
      if (!editor || !wrapper) return false
      const root = editor.root as HTMLElement
      if (!root) return false

      // 常驻 overlay，按需显示
      const overlay = document.createElement('div')
      overlay.className = 'quill-img-resize-overlay'
      overlay.style.display = 'none'
      const handle = document.createElement('div')
      handle.className = 'quill-img-resize-handle'
      handle.title = '拖拽调整图片大小'
      overlay.appendChild(handle)
      wrapper.appendChild(overlay)

      // —— 手动尺寸面板：宽/高输入等比联动，应用时统一写为 width 属性 ——
      const panel = document.createElement('div')
      panel.className = 'quill-img-resize-panel'
      const widthInput = document.createElement('input')
      widthInput.type = 'text'
      widthInput.inputMode = 'numeric'
      widthInput.className = 'quill-img-size-input'
      widthInput.title = '宽度 (px)，高度自动等比，回车应用'
      const timesLabel = document.createElement('span')
      timesLabel.className = 'quill-img-resize-times'
      timesLabel.textContent = '×'
      const heightInput = document.createElement('input')
      heightInput.type = 'text'
      heightInput.inputMode = 'numeric'
      heightInput.className = 'quill-img-size-input'
      heightInput.title = '高度 (px)，宽度自动等比，回车应用'
      const applyBtn = document.createElement('button')
      applyBtn.type = 'button'
      applyBtn.className = 'quill-img-resize-btn'
      applyBtn.textContent = '确定'
      const resetBtn = document.createElement('button')
      resetBtn.type = 'button'
      resetBtn.className = 'quill-img-resize-btn'
      resetBtn.textContent = '重置'
      resetBtn.title = '恢复图片原始尺寸与对齐'
      panel.append(widthInput, timesLabel, heightInput, applyBtn, resetBtn)

      // —— 对齐：三个互斥按钮，写 img-align-* class（前台白名单不放行 style，class 才能生效） ——
      const sep = document.createElement('span')
      sep.className = 'quill-img-panel-sep'
      sep.textContent = '|'
      panel.appendChild(sep)

      const alignButtons = IMAGE_ALIGN_VALUES.map((align) => {        const btn = document.createElement('button')
        btn.type = 'button'
        btn.className = 'quill-img-resize-btn'
        btn.dataset.align = align
        btn.textContent = align === 'left' ? '左' : align === 'center' ? '中' : '右'
        btn.title = `图片${btn.textContent}对齐`
        panel.appendChild(btn)
        return btn
      })

      // —— 替换图片 ——
      const replaceBtn = document.createElement('button')
      replaceBtn.type = 'button'
      replaceBtn.className = 'quill-img-resize-btn'
      replaceBtn.textContent = '替换'
      replaceBtn.title = '上传新图片替换当前图片（保留尺寸与对齐）'
      panel.appendChild(replaceBtn)
      const replaceInput = document.createElement('input')
      replaceInput.type = 'file'
      replaceInput.accept = 'image/jpeg,image/png,image/webp,image/gif'
      replaceInput.style.display = 'none'
      panel.appendChild(replaceInput)

      // —— Alt 文本（无障碍与 SEO）——
      const altInput = document.createElement('input')
      altInput.type = 'text'
      altInput.className = 'quill-img-size-input quill-img-alt-input'
      altInput.placeholder = 'Alt 文本'
      altInput.title = '图片替代文本，回车应用（读屏软件与搜索引擎会读取）'
      panel.appendChild(altInput)

      overlay.appendChild(panel)

      let targetImg: HTMLImageElement | null = null

      /** 图片宽高比（优先原始像素比，未加载完成时退回渲染比） */
      const getRatio = (img: HTMLImageElement): number => {
        if (img.naturalWidth > 0 && img.naturalHeight > 0) {
          return img.naturalWidth / img.naturalHeight
        }
        const r = img.getBoundingClientRect()
        return r.height > 0 ? r.width / r.height : 1
      }

      /** 宽度上限：图片所在块内容宽度（与拖拽缩放一致），下限见 IMAGE_RESIZE_MIN_WIDTH */
      const clampWidth = (img: HTMLImageElement, w: number): number => {
        const maxWidth = Math.max(IMAGE_RESIZE_MIN_WIDTH, (img.parentElement?.clientWidth ?? 800) - 8)
        return Math.min(maxWidth, Math.max(IMAGE_RESIZE_MIN_WIDTH, Math.round(w)))
      }

      /** 面板输入框与当前图片渲染尺寸同步（聚焦中的输入框不覆盖，避免打断输入） */
      const syncPanel = () => {
        if (!targetImg) return
        const r = targetImg.getBoundingClientRect()
        if (document.activeElement !== widthInput) widthInput.value = String(Math.round(r.width))
        if (document.activeElement !== heightInput) heightInput.value = String(Math.round(r.height))
        if (document.activeElement !== altInput) altInput.value = targetImg.getAttribute('alt') || ''
        alignButtons.forEach((btn) => {
          const active = targetImg?.classList.contains(imageAlignClass(btn.dataset.align as ImageAlign))
          btn.classList.toggle('ql-active', !!active)
        })
      }

      const position = () => {
        if (!targetImg) return
        const ir = targetImg.getBoundingClientRect()
        const wr = wrapper.getBoundingClientRect()
        overlay.style.left = `${ir.left - wr.left}px`
        overlay.style.top = `${ir.top - wr.top}px`
        overlay.style.width = `${ir.width}px`
        overlay.style.height = `${ir.height}px`
        syncPanel()
      }

      const hide = () => {
        targetImg = null
        overlay.style.display = 'none'
      }

      // 目标图片仍在文档中则重新定位（滚动/编辑/格式化后），已被删除则隐藏
      const sync = () => {
        if (!targetImg || !root.contains(targetImg)) hide()
        else position()
      }

      // 点击图片：显示调整框；点击其它区域：隐藏
      const onClick = (e: MouseEvent) => {
        const t = e.target
        if (t instanceof HTMLImageElement && root.contains(t)) {
          targetImg = t
          overlay.style.display = 'block'
          position()
        } else {
          hide()
        }
      }
      root.addEventListener('click', onClick)
      cleanups.push(() => root.removeEventListener('click', onClick))

      // Esc 关闭调整框
      const onKey = (e: KeyboardEvent) => {
        if (e.key === 'Escape') hide()
      }
      window.addEventListener('keydown', onKey)
      cleanups.push(() => window.removeEventListener('keydown', onKey))

      // 编辑器滚动 / 窗口缩放：overlay 跟随图片
      const scrollContainer = editor.container as HTMLElement
      const onScrollOrResize = () => sync()
      scrollContainer.addEventListener('scroll', onScrollOrResize)
      window.addEventListener('resize', onScrollOrResize)
      cleanups.push(() => {
        scrollContainer.removeEventListener('scroll', onScrollOrResize)
        window.removeEventListener('resize', onScrollOrResize)
      })

      // 内容变化（切章载入 / 删除图片 / clean 清除属性）：同步或隐藏
      editor.on('text-change', sync)
      cleanups.push(() => editor.off('text-change', sync))

      // 按住右下角手柄拖拽：等比调整宽度，松手后落库
      const onHandleMouseDown = (e: MouseEvent) => {
        if (!targetImg) return
        e.preventDefault() // 阻止原生 img 拖拽与文本选中
        const img = targetImg
        const startX = e.clientX
        const rect = img.getBoundingClientRect()
        const startWidth = rect.width
        // 上限取图片所在块（p/td 等）的内容宽度，避免拖出容器后被 max-width 钳制
        const maxWidth = Math.max(
          IMAGE_RESIZE_MIN_WIDTH,
          (img.parentElement?.clientWidth ?? startWidth) - 8,
        )

        const onMouseMove = (ev: MouseEvent) => {
          const w = Math.min(
            maxWidth,
            Math.max(IMAGE_RESIZE_MIN_WIDTH, Math.round(startWidth + ev.clientX - startX)),
          )
          // 拖拽期间用 style 预览（CSS height:auto 保证等比）
          img.style.width = `${w}px`
          position()
        }
        const onMouseUp = () => {
          document.removeEventListener('mousemove', onMouseMove)
          document.removeEventListener('mouseup', onMouseUp)
          if (!root.contains(img)) return // 拖拽过程中图片被外部逻辑移除
          const finalWidth = Math.round(img.getBoundingClientRect().width)
          img.style.width = '' // 清除预览，尺寸改由 width 属性承载
          commitWidth(img, finalWidth)
        }
        document.addEventListener('mousemove', onMouseMove)
        document.addEventListener('mouseup', onMouseUp)
      }
      handle.addEventListener('mousedown', onHandleMouseDown)
      cleanups.push(() => handle.removeEventListener('mousedown', onHandleMouseDown))

      // —— 写入 image blot ——
      // blot.format 只改 DOM，需主动触发一次 diff 让 Quill 发出
      // text-change（source=user → onChange → 表单值同步保存）
      const applyFormat = (img: HTMLImageElement, name: string, val: string | null) => {
        const blot = Quill.find(img) as { format?: (n: string, v: string | null) => void } | null
        if (blot?.format) blot.format(name, val)
        else if (val) img.setAttribute(name, val)
        else img.removeAttribute(name)
        editor.update(Quill.sources.USER)
        if (targetImg === img) position()
      }

      /** 将宽度写入 image blot，并清除 height 属性统一为「纯 width 等比」模型 */
      const commitWidth = (img: HTMLImageElement, w: number) => {
        applyFormat(img, 'width', String(clampWidth(img, w)))
        applyFormat(img, 'height', null)
      }

      // 宽度输入：高度按等比换算显示。钳制不在输入期间回写（会覆盖逐字符中间值导致无法重输），推迟到「确定」时
      const onWidthInput = () => {
        const img = targetImg
        if (!img) return
        const w = parseInt(widthInput.value, 10)
        if (!Number.isFinite(w) || w <= 0) return
        heightInput.value = String(Math.max(1, Math.round(w / getRatio(img))))
      }

      // 高度输入：宽度按等比换算回填（同样不在输入期间回写钳制值）
      const onHeightInput = () => {
        const img = targetImg
        if (!img) return
        const h = parseInt(heightInput.value, 10)
        if (!Number.isFinite(h) || h <= 0) return
        widthInput.value = String(Math.max(1, Math.round(h * getRatio(img))))
      }

      const applyPanel = () => {
        const img = targetImg
        if (!img) return
        const w = parseInt(widthInput.value, 10)
        if (!Number.isFinite(w) || w <= 0) {
          syncPanel() // 非法输入恢复为当前尺寸
          return
        }
        // 应用前先失焦：syncPanel 不覆盖聚焦中的输入框，失焦后面板才能同步为钳制后的实际尺寸
        widthInput.blur()
        heightInput.blur()
        commitWidth(img, w)
      }

      /** 重置：清除 width/height/对齐，恢复原始尺寸与默认对齐 */
      const resetImage = () => {
        const img = targetImg
        if (!img) return
        applyFormat(img, 'width', null)
        applyFormat(img, 'height', null)
        applyFormat(img, 'image-align', null)
        position()
      }

      /** 对齐按钮：再次点击同一对齐即取消（与段落对齐一致的交互习惯） */
      const applyAlign = (align: ImageAlign) => {
        const img = targetImg
        if (!img) return
        const isActive = img.classList.contains(imageAlignClass(align))
        applyFormat(img, 'image-align', isActive ? null : align)
      }

      /** Alt 文本：写入 image blot 的 alt 属性（前台白名单已允许） */
      const applyAlt = () => {
        const img = targetImg
        if (!img) return
        const next = altInput.value.trim()
        applyFormat(img, 'alt', next || null)
      }

      /** 替换图片：上传新文件后用同位置的 image embed 顶替，并保留原有尺寸与对齐 */
      const doReplace = async () => {
        const img = targetImg
        replaceInput.value = ''
        const file = replaceInput.files?.[0]
        if (!file || !img) return
        // Quill.find 返回类型过宽，按 getIndex 入参类型收窄
        const blot = Quill.find(img) as Parameters<Quill['getIndex']>[0] | null
        if (!blot) return
        const index = editor.getIndex(blot)
        const keepWidth = img.getAttribute('width')
        const keepAlt = img.getAttribute('alt')
        const keepAlign = IMAGE_ALIGN_VALUES.find((v) => img.classList.contains(imageAlignClass(v)))
        try {
          const { url } = await uploadImage(file)
          editor.deleteText(index, 1, 'user')
          editor.insertEmbed(index, 'image', url, 'user')
          if (keepWidth) editor.formatText(index, 1, { width: keepWidth })
          if (keepAlt) editor.formatText(index, 1, { alt: keepAlt })
          if (keepAlign) editor.formatText(index, 1, { 'image-align': keepAlign })
          const [leaf] = editor.getLeaf(index)
          const next = (leaf as unknown as { domNode?: HTMLImageElement })?.domNode
          if (next instanceof HTMLImageElement) {
            targetImg = next
            position()
          }
        } catch (err) {
          toast.error(err instanceof Error ? err.message : '图片替换失败')
        }
      }
      replaceBtn.addEventListener('click', () => replaceInput.click())
      replaceInput.addEventListener('change', () => { void doReplace() })

      widthInput.addEventListener('input', onWidthInput)
      heightInput.addEventListener('input', onHeightInput)
      const onInputKey = (e: KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          applyPanel()
        }
      }
      const onAltKey = (e: KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          applyAlt()
        }
      }
      widthInput.addEventListener('keydown', onInputKey)
      heightInput.addEventListener('keydown', onInputKey)
      altInput.addEventListener('keydown', onAltKey)
      altInput.addEventListener('blur', applyAlt)
      applyBtn.addEventListener('click', applyPanel)
      resetBtn.addEventListener('click', resetImage)
      alignButtons.forEach((btn) => {
        btn.addEventListener('click', () => applyAlign(btn.dataset.align as ImageAlign))
      })
      cleanups.push(() => {
        widthInput.removeEventListener('input', onWidthInput)
        heightInput.removeEventListener('input', onHeightInput)
        widthInput.removeEventListener('keydown', onInputKey)
        heightInput.removeEventListener('keydown', onInputKey)
        altInput.removeEventListener('keydown', onAltKey)
        altInput.removeEventListener('blur', applyAlt)
        applyBtn.removeEventListener('click', applyPanel)
        resetBtn.removeEventListener('click', resetImage)
        replaceInput.removeEventListener('change', doReplace)
      })

      cleanups.push(() => overlay.remove())
      return true
    }

    // 编辑器初始化有延迟，首次未就绪则短延迟重试
    if (!attach()) {
      retryTimer = setTimeout(attach, 100)
    }
    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
      cleanups.forEach((fn) => fn())
      cleanups.length = 0
    }
  }, [])

  // 新增能力的工具栏处理器

  /** 水平分割线：块级原子元素，直接 insertEmbed 到光标处 */
  const handleInsertHr = useCallback(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    const range = quill.getSelection(true) || { index: quill.getLength() - 1, length: 0 }
    quill.insertEmbed(range.index, 'hr', true, 'user')
    quill.setSelection(range.index + 1, 0, 'silent')
  }, [])

  /**
   * 提示框：再次选择当前类型即取消（还原为普通段落）。
   * 依赖 CalloutBlock 的 formats()/Parchment 的 replaceWith 语义，
   * 这里只负责把「当前类型 == 目标类型」翻译成 false。
   */
  const handleCallout = useCallback((value: string) => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    const range = quill.getSelection(true)
    if (!range) return
    const current = (quill.getFormat(range.index) as { callout?: string }).callout
    const next = !value || current === value ? false : value
    quill.format('callout', next, 'user')
  }, [])

  /** 撤销 / 重做（history 模块在 modules 中显式配置，栈上限 200 步） */
  const handleHistory = useCallback((direction: 'undo' | 'redo') => {
    const history = quillRef.current?.getEditor()?.getModule('history') as
      | { undo?: () => void; redo?: () => void }
      | undefined
    history?.[direction]?.()
  }, [])

  /** 确认插入视频：解析平台地址 → 插入 iframe embed */
  const confirmVideo = useCallback(() => {
    const parsed = parseVideoUrl(videoUrl)
    if (!parsed) {
      toast.error(`无法识别的视频地址。${VIDEO_HINT}`)
      return
    }
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    const range = quill.getSelection(true) || { index: quill.getLength() - 1, length: 0 }
    quill.insertEmbed(range.index, 'video', parsed.embedUrl, 'user')
    quill.setSelection(range.index + 1, 0, 'silent')
    setVideoOpen(false)
    setVideoUrl('')
    toast.success(`已插入${parsed.platform}视频`)
  }, [videoUrl])

  /** 进入 / 应用 HTML 源码模式 */
  const applySource = useCallback(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    try {
      // clipboard.convert 走 Quill 自身的 HTML → Delta 解析（含表格、自定义 blot），
      // 比手写解析器可靠；返回的 Delta 由 setContents 归一化结尾换行
      const clipboard = quill.getModule('clipboard') as unknown as {
        convert: (arg: { html: string }) => Delta
      }
      const delta = clipboard.convert({ html: sourceText })
      quill.setContents(delta, 'user')
      setSourceMode(false)
      toast.success('源码已应用到编辑器')
    } catch (err) {
      console.warn('源码解析失败:', err)
      toast.error('HTML 解析失败，请检查标签是否闭合')
    }
  }, [sourceText])

  /**
   * 「应用源码」的最新引用：源码模式下每敲一个字符 sourceText 都变，
   * 若直接进 toggleSourceMode 依赖会每次按键都重建 modules，react-quill 便
   * 销毁重建编辑器（撤销栈清空、粘贴/图片 overlay 失效）。用 ref 保持回调稳定。
   */
  const applySourceRef = useRef(applySource)
  useEffect(() => {
    applySourceRef.current = applySource
  }, [applySource])

  const toggleSourceMode = useCallback(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    if (sourceModeRef.current) {
      applySourceRef.current()
      return
    }
    setImageSearchOpen(false)
    setSourceText(quill.getSemanticHTML())
    setSourceMode(true)
  }, [])

  /** 打开预览：把当前正文的语义化 HTML 消毒后渲染 */
  const openPreview = useCallback(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    setPreviewHtml(sanitizePreviewHtml(quill.getSemanticHTML()))
    setPreviewOpen(true)
  }, [])

  // 查找替换动作

  /** 定位到下一个命中项并滚动到可视区中部 */
  const gotoMatch = useCallback((match: FindMatch) => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return
    quill.setSelection(match.index, match.length, 'silent')
    const root = quill.root as HTMLElement
    const bounds = quill.getBounds(match.index, match.length)
    if (bounds && root) {
      root.scrollTop = Math.max(0, root.scrollTop + bounds.top - root.clientHeight / 2)
    }
    quill.focus()
  }, [])

  const findNext = useCallback(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill || !findText) return
    const matches = collectMatches(quill, findText, findCaseSensitive)
    setFindCount(matches.length)
    if (matches.length === 0) {
      toast.info('未找到匹配内容')
      return
    }
    const cursor = quill.getSelection()?.index ?? -1
    const next = matches.find((m) => m.index > cursor) ?? matches[0]
    gotoMatch(next)
  }, [findText, findCaseSensitive, gotoMatch])

  const replaceCurrent = useCallback(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill || !findText) return
    const matches = collectMatches(quill, findText, findCaseSensitive)
    setFindCount(matches.length)
    if (matches.length === 0) {
      toast.info('未找到匹配内容')
      return
    }
    const sel = quill.getSelection()
    const target =
      (sel && sel.length > 0 ? matches.find((m) => m.index === sel.index) : undefined) ??
      matches.find((m) => m.index >= (sel?.index ?? 0)) ??
      matches[0]
    quill.deleteText(target.index, target.length, 'user')
    if (replaceText) quill.insertText(target.index, replaceText, 'user')
    quill.setSelection(target.index + replaceText.length, 0, 'silent')
    // 替换会改变后续索引，重新统计命中数
    setFindCount(collectMatches(quill, findText, findCaseSensitive).length)
  }, [findText, replaceText, findCaseSensitive])

  const replaceAll = useCallback(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill || !findText) return
    const matches = collectMatches(quill, findText, findCaseSensitive)
    if (matches.length === 0) {
      toast.info('未找到匹配内容')
      return
    }
    // 从后往前替换：前面的替换不会让后面待处理项的索引失效
    for (let i = matches.length - 1; i >= 0; i -= 1) {
      quill.deleteText(matches[i].index, matches[i].length, 'user')
      if (replaceText) quill.insertText(matches[i].index, replaceText, 'user')
    }
    setFindCount(0)
    toast.success(`已替换 ${matches.length} 处`)
  }, [findText, replaceText, findCaseSensitive])

  // modules 仅在 handler 变化时重建：table-better 为表格模块；keyboard 合并表格导航
  // 与段落结构快捷键（Quill addBinding 按 key 分桶共存不覆盖）；history 显式配撤销栈；
  // table:false 禁用内置基础表格模块避免冲突
  const modules = useMemo(() => ({
    toolbar: {
      container: QUILL_TOOLBAR_CONTAINER,
      handlers: {
        image: handleImageUpload,
        attachment: handleAttachmentUpload,
        video: () => setVideoOpen(true),
        hr: handleInsertHr,
        callout: handleCallout,
        undo: () => handleHistory('undo'),
        redo: () => handleHistory('redo'),
        find: () => setFindOpen(true),
        source: toggleSourceMode,
        preview: openPreview,
        help: () => setHelpOpen(true),
        fullscreen: toggleFullscreen,
        ai: () => onAiOpenRef.current?.(),
        'search-image': openImageSearch,
      },
    },
    'table-better': {
      language: 'zh_CN',
      toolbarTable: true,
    },
    keyboard: {
      // Quill 的 addBinding 按 key 分桶共存，两边按键不重叠时展开合并是安全的
      bindings: {
        ...QuillTableBetter.keyboardBindings,
        ...STRUCTURE_KEYBOARD_BINDINGS,
      },
    },
    history: {
      delay: 800,
      maxStack: 200,
      userOnly: true,
    },
    table: false,
  }), [
    handleImageUpload, handleAttachmentUpload, handleInsertHr, handleCallout,
    handleHistory, toggleSourceMode, openPreview, toggleFullscreen, openImageSearch,
  ])

  // 工具栏增强：注入自定义按钮图标、补写下拉选项中文文案、同步全屏/源码按钮状态
  useEffect(() => {
    let cancelled = false
    const decorate = (): boolean => {
      if (cancelled) return true
      let editor: ReturnType<ReactQuill['getEditor']> | undefined
      try {
        editor = quillRef.current?.getEditor()
      } catch {
        return false
      }
      if (!editor) return false
      const toolbarModule = editor.getModule('toolbar') as
        | { container?: HTMLElement }
        | undefined
      const container = toolbarModule?.container
      if (!container) return false

      // 1) 自定义按钮图标
      Object.entries(CUSTOM_ICONS).forEach(([format, svg]) => {
        if (format === 'fullscreen') return
        const btn = container.querySelector(`.ql-${format}`) as HTMLButtonElement | null
        if (btn && !btn.innerHTML.trim()) btn.innerHTML = svg
      })

      // 2) 补齐下拉的选项中文文案（补写规则见 applySelectOptionLabels）
      applySelectOptionLabels(container)

      // 3) 状态类按钮
      const fullscreenBtn = container.querySelector('.ql-fullscreen') as HTMLButtonElement | null
      if (fullscreenBtn) {
        fullscreenBtn.innerHTML = isFullscreen ? FULLSCREEN_EXIT_SVG : FULLSCREEN_ENTER_SVG
        fullscreenBtn.title = isFullscreen ? '退出全屏 (Esc)' : '全屏编辑'
      }
      const sourceBtn = container.querySelector('.ql-source') as HTMLButtonElement | null
      if (sourceBtn) sourceBtn.classList.toggle('ql-active', sourceMode)

      // 页面未启用 AI 时入口不占工具栏位置；用 display 而非移除节点，避免 Quill 重建工具栏
      const aiBtn = container.querySelector('.ql-ai') as HTMLButtonElement | null
      if (aiBtn) aiBtn.style.display = aiEnabled ? '' : 'none'

      // 图片搜索按后台开关显示；源码模式下保留按钮但禁用，避免入口突然消失
      const searchBtn = container.querySelector('.ql-search-image') as HTMLButtonElement | null
      if (searchBtn) {
        searchBtn.style.display = imageSearchAvailable ? '' : 'none'
        searchBtn.disabled = sourceMode
        searchBtn.title = sourceMode ? '请先退出源码模式' : '搜索图片（图库）'
      }

      return true
    }
    // 编辑器初始化有延迟，首次未就绪则短延迟重试
    if (!decorate()) {
      const timer = setTimeout(decorate, 100)
      return () => { cancelled = true; clearTimeout(timer) }
    }
    return () => { cancelled = true }
  }, [isFullscreen, sourceMode, aiEnabled, imageSearchAvailable])

  // 为工具栏按钮注入 title 悬停提示（含快捷键说明）
  useEffect(() => {
    let cancelled = false
    const setTitles = (): boolean => {
      if (cancelled) return true
      let editor: ReturnType<ReactQuill['getEditor']> | undefined
      try {
        editor = quillRef.current?.getEditor()
      } catch {
        return false
      }
      if (!editor) return false
      const toolbarModule = editor.getModule('toolbar') as
        | { container?: HTMLElement }
        | undefined
      const container = toolbarModule?.container
      if (!container) return false
      for (const [selector, title] of TOOLBAR_TITLES) {
        const el = container.querySelector(selector) as HTMLElement | null
        if (el && !el.title) el.title = title
      }
      return true
    }
    // 编辑器初始化有延迟，首次未就绪则短延迟重试
    if (!setTitles()) {
      const timer = setTimeout(setTitles, 100)
      return () => { cancelled = true; clearTimeout(timer) }
    }
    return () => { cancelled = true }
  }, [])

  // 全屏模式下按 Esc 退出
  useEffect(() => {
    if (!isFullscreen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        isFullscreenRef.current = false
        setIsFullscreen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isFullscreen])

  // Ctrl/Cmd + F 打开查找面板；挂编辑器容器并捕获阶段，保证正文与面板输入框内都能触发
  useEffect(() => {
    const host = editorWrapperRef.current
    if (!host) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        e.stopPropagation()
        setFindOpen(true)
        setTimeout(() => findInputRef.current?.select(), 0)
      }
    }
    host.addEventListener('keydown', onKey, true)
    return () => host.removeEventListener('keydown', onKey, true)
  }, [])

  // 打开查找面板时自动聚焦输入框
  useEffect(() => {
    if (findOpen) setTimeout(() => findInputRef.current?.focus(), 0)
  }, [findOpen])

  // 从编辑器 DOM 提取标题生成目录
  const extractToc = useCallback(() => {
    let editor: ReturnType<ReactQuill['getEditor']> | undefined
    try {
      editor = quillRef.current?.getEditor()
    } catch {
      // 编辑器尚未实例化，跳过本次提取
      return
    }
    if (!editor) return
    const root = editor.root as HTMLElement
    const headings = root.querySelectorAll('h1, h2, h3, h4, h5, h6')
    const items: TocItem[] = []
    headings.forEach((el, index) => {
      const level = parseInt(el.tagName[1], 10)
      const text = el.textContent?.trim() || `标题 ${index + 1}`
      const id = `toc-heading-${index}`
      // 用 data-toc 而非 el.id 标记锚点：id 会随内容入库，多章节同屏渲染时重复冲突
      el.setAttribute('data-toc', id)
      items.push({ id, text, level })
    })
    // 内容未变化时返回 prev，跳过不必要的 setState 与重渲染
    setToc((prev) => {
      if (
        prev.length === items.length &&
        prev.every((p, i) => p.id === items[i].id && p.text === items[i].text && p.level === items[i].level)
      ) {
        return prev
      }
      return items
    })
  }, [])

  // 内容变化时更新目录（防抖等待 DOM 更新完成）
  useEffect(() => {
    const timer = setTimeout(extractToc, TOC_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [value, extractToc])

  const scrollToHeading = useCallback((id: string) => {
    const editor = quillRef.current?.getEditor()
    if (!editor) return
    const root = editor.root as HTMLElement
    const el = root.querySelector(`[data-toc="${id}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [])

  // 仅在尺寸 prop 变化时重建样式串（静态排版规则已由上方 effect 全局注入）
  const editorStyleCss = useMemo(() => {
    const h = resolveSize(height, DEFAULT_HEIGHT)
    const mh = resolveSize(minHeight, DEFAULT_MIN_HEIGHT)
    return `
  .quill-editor-wrapper {
    height: ${autoHeight ? 'auto' : h};
    min-height: ${mh};
    display: flex;
    flex-direction: column;
  }
  .quill-editor-wrapper > .quill {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  .quill-editor-wrapper .ql-container {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }
  /* 修 Chrome 控制台 [Violation] Added non-passive event listener to a scroll-blocking
     'touchstart' event —— Quill 2.0.3 在 '.ql-editor'（contenteditable）上注册了非 passive 的 touchstart
     监听器，每实例化一次就警告一条（DevTools 堆栈上显示的是初始化经过的 QuillEditor.tsx:68，
     但实际监听器挂载点不是 .ql-container，所以**两个选择器都要设**）。
     声明 touch-action 只允许浏览器处理垂直 pan，浏览器就不再要求 passive，警告消失。
     Quill 选区拖拽走 pointer 事件，不受 pan-y 影响。
     边界：移动端若长按选区失效，把 pan-y 改为 pan-x pan-y 即可。 */
  .quill-editor-wrapper .ql-container,
  .quill-editor-wrapper .ql-editor {
    touch-action: pan-y;
  }
  .quill-editor-wrapper .ql-editor {
    flex: 1;
    min-height: ${autoHeight ? h : 'auto'} !important;
  }
  .quill-toc-container {
    height: ${autoHeight ? 'auto' : h};
  }
  ${
    autoHeight
      ? `/* ---- 自适应高度（长文档吸顶）----
     ⚠️ 整条链路必须**全部改成块级流式**，包括 wrapper 本身。
     实测（CDP）：只把 .quill / .ql-container 改成 block、wrapper 仍是 flex 时，
     列方向 flex 会把 .quill 压缩到比内容矮（.quill 363 vs 内容 495），
     于是 .ql-container 溢出到 wrapper 之外：正文框那条 #ccc 下边框被推到状态栏下面
     看不见，状态栏看起来像浮在正文上 —— 与教程编辑页（定高、边框完整）观感不一致。
     全块级后各层高度=内容高度，边框、状态栏、间距与教程页完全一致。 */
  .quill-editor-wrapper {
    height: auto;
    display: block;
  }
  .quill-editor-wrapper > .quill {
    flex: none;
    display: block;
  }
  .quill-editor-wrapper .ql-container {
    flex: none;
    display: block;
    overflow: visible;
  }
  /* 源码模式 textarea 在静态样式里是 flex:1 + min-height:0（依赖定高容器），
     自适应高度下会缩成默认两行，故按 height 兜底。选择器多一层 .quill-root-layout
     提高优先级，避免被 head 里的静态规则压过。 */
  .quill-root-layout .quill-editor-wrapper.quill-source-mode .quill-source-textarea {
    min-height: ${h};
  }`
      : ''
  }`
  }, [height, minHeight, autoHeight])

  return (
    <div className={`quill-root-layout${isFullscreen ? ' quill-fullscreen' : ''}`}>
      <div
        className={`quill-editor-wrapper${sourceMode ? ' quill-source-mode' : ''}`}
        ref={editorWrapperRef}
      >
        {findOpen && (
          <div className="quill-find-panel">
            <input
              ref={findInputRef}
              value={findText}
              onChange={(e) => { setFindText(e.target.value); setFindCount(null) }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  findNext()
                }
              }}
              placeholder="查找内容"
              aria-label="查找内容"
            />
            <input
              value={replaceText}
              onChange={(e) => setReplaceText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  replaceCurrent()
                }
              }}
              placeholder="替换为"
              aria-label="替换为"
            />
            <button type="button" onClick={findNext}>查找下一个</button>
            <button type="button" onClick={replaceCurrent}>替换</button>
            <button type="button" onClick={replaceAll}>全部替换</button>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: '#64748b' }}>
              <input
                type="checkbox"
                checked={findCaseSensitive}
                onChange={(e) => { setFindCaseSensitive(e.target.checked); setFindCount(null) }}
                style={{ minWidth: 'auto', height: 'auto', width: 14 }}
              />
              区分大小写
            </label>
            <span className="quill-find-count">
              {findCount === null ? '' : `${findCount} 处匹配`}
            </span>
            <button type="button" className="quill-find-close" onClick={() => setFindOpen(false)}>
              关闭
            </button>
          </div>
        )}

        {sourceMode && (
          <div className="quill-find-panel">
            <span style={{ color: '#475569' }}>HTML 源码模式</span>
            <span className="quill-find-count">修改后点击「应用」写回编辑器</span>
            <button type="button" onClick={applySource}>应用</button>
            <button type="button" className="quill-find-close" onClick={() => setSourceMode(false)}>
              取消
            </button>
          </div>
        )}

        <ReactQuill
          ref={quillRef}
          theme="snow"
          modules={modules}
          formats={QUILL_FORMATS}
          placeholder={placeholder || '请输入内容...'}
          value={value || ''}
          onChange={handleChange}
        />

        {sourceMode && (
          <textarea
            className="quill-source-textarea"
            value={sourceText}
            onChange={(e) => setSourceText(e.target.value)}
            spellCheck={false}
            aria-label="HTML 源码"
          />
        )}

        <div className="quill-statusbar">
          <span>字符 <b>{stats.chars}</b></span>
          <span>单词 <b>{stats.words}</b></span>
          <span>段落 <b>{stats.blocks}</b></span>
          {stats.minutes > 0 && <span>约 <b>{stats.minutes}</b> 分钟读完</span>}
          <span className="quill-statusbar-spacer" />
          <button type="button" className="quill-statusbar-hint" onClick={() => setHelpOpen(true)}>
            快捷键与功能说明
          </button>
        </div>

        <style>{editorStyleCss}</style>
      </div>

      {showToc && <TocPanel items={toc} onSelect={scrollToHeading} />}

      <Modal
        title="插入在线视频"
        open={videoOpen}
        onCancel={() => setVideoOpen(false)}
        onOk={confirmVideo}
        okText="插入"
        cancelText="取消"
        destroyOnHidden
      >
        <Input
          autoFocus
          value={videoUrl}
          onChange={(e) => setVideoUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); confirmVideo() } }}
          placeholder="粘贴视频链接或 BV 号，如 BV1xx411c7mD"
        />
        <div style={{ marginTop: 8, fontSize: 12, color: '#94a3b8', lineHeight: 1.7 }}>{VIDEO_HINT}</div>
      </Modal>

      <PreviewModal open={previewOpen} html={previewHtml} onClose={() => setPreviewOpen(false)} />

      <ImageSearchModal
        open={imageSearchOpen}
        initialQuery={imageSearchQuery}
        onClose={() => setImageSearchOpen(false)}
        onInsert={handleImageInsert}
      />

      <ShortcutHelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  )
}

