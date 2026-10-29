/**
 * 后台富文本编辑器的 Quill 扩展层：注册自定义 blot 并提供视频解析等纯函数。
 * 新增块级内容一律做成 blot：Quill 的语义化序列化只认 blot，只改 DOM 不进
 * blot 模型的写法在保存瞬间会被丢弃；序列化产物须能被自己重新解析（往返一致）。
 */

import { Quill } from 'react-quill-new'

/* Quill.import 的返回类型在 react-quill-new 中未收敛，这里统一按构造器使用 */
/* eslint-disable @typescript-eslint/no-explicit-any */
type AnyBlot = any

const BlockEmbed: AnyBlot = Quill.import('blots/block/embed')
const Block: AnyBlot = Quill.import('blots/block')
const ImageBlot: AnyBlot = Quill.import('formats/image')
const VideoBlot: AnyBlot = Quill.import('formats/video')

/** 转义为 HTML 文本节点内容（属性值另用 escapeAttr） */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** 转义为 HTML 属性值（引号必须转义，否则可越出属性） */
function escapeAttr(text: string): string {
  return escapeHtml(text).replace(/"/g, '&quot;')
}

// 1. 水平分割线（Quill 2 没有内置 hr blot）

/** 水平分割线：做成 BlockEmbed 使其独占一行、可整块选中删除 */
class HorizontalRule extends BlockEmbed {
  static blotName = 'hr'
  static tagName = 'HR'

  /** BlockEmbed.delta() 会取 value() 作为 op 的 insert 值，必须显式返回，否则为 undefined */
  static value(): boolean {
    return true
  }
}

// 2. 提示框（Callout）

/** 支持的提示框类型，顺序即工具栏下拉顺序 */
export const CALLOUT_TYPES = ['info', 'warning', 'success', 'danger'] as const
type CalloutType = (typeof CALLOUT_TYPES)[number]

/** 提示框类型的中文名（工具栏下拉与帮助面板共用） */
export const CALLOUT_LABELS: Record<CalloutType, string> = {
  info: '信息提示',
  warning: '注意事项',
  success: '成功示例',
  danger: '风险警告',
}

/**
 * 提示框：Block 子类以承载富文本内容，类型存 data-callout 属性。
 * Parchment 的 format 语义使「切换类型 / 取消还原为段落」无需自定义逻辑。
 */
export class CalloutBlock extends Block {
  static blotName = 'callout'
  static tagName = 'DIV'
  static className = 'ql-callout'

  static create(value: unknown) {
    const node = super.create(value) as HTMLElement
    if (typeof value === 'string' && value) node.setAttribute('data-callout', value)
    return node
  }

  /** 返回当前类型；始终返回合法值（缺省 info），避免下拉无法定位选中项 */
  static formats(domNode: HTMLElement): CalloutType {
    const value = domNode.getAttribute('data-callout')
    return (CALLOUT_TYPES as readonly string[]).includes(value || '')
      ? (value as CalloutType)
      : 'info'
  }
}

// 3. 附件下载卡片

/** 附件 blot 承载的数据（同时写入 data-* 属性，供 Clipboard 还原） */
interface AttachmentValue {
  /** 文件地址（COS URL） */
  href: string
  /** 展示用文件名 */
  name: string
  /** 展示用文件大小，如 "1.2 MB"，可空 */
  size?: string
}

/** 生成附件卡片的内部结构（唯一来源）：编辑器内 DOM 与序列化产物必须一致，否则无法被 Clipboard 还原 */
function renderAttachmentInner(href: string, name: string, size: string): string {
  const sizeHtml = size ? `<span class="ql-attachment-size">${escapeHtml(size)}</span>` : ''
  return (
    `<a class="ql-attachment-link" href="${escapeAttr(href)}" target="_blank" rel="noopener noreferrer" download>` +
    `<span class="ql-attachment-name">${escapeHtml(name)}</span>${sizeHtml}` +
    `</a>`
  )
}

/**
 * 附件下载卡片：BlockEmbed 块级原子元素，光标只能整体选中删除。
 * tagName 用 DIV 而非 A（Quill 要求 BlockEmbed 用块级标签，行内标签破坏光标定位）。
 */
class FileAttachment extends BlockEmbed {
  static blotName = 'attachment'
  static tagName = 'DIV'
  // 刻意不叫 ql-attachment：工具栏按钮的 class 也是 ql-attachment，
  // 同名会让 Parchment 的 classes 注册表与工具栏 DOM 语义混淆（排查困难）
  static className = 'ql-file-attachment'

  static create(value: AttachmentValue | string) {
    const node = super.create(value) as HTMLElement
    node.setAttribute('contenteditable', 'false')
    const href = typeof value === 'string' ? value : value?.href || ''
    const name = typeof value === 'string' ? value : value?.name || ''
    const size = typeof value === 'string' ? '' : value?.size || ''
    node.setAttribute('data-href', href)
    node.setAttribute('data-name', name)
    if (size) node.setAttribute('data-size', size)
    node.innerHTML = renderAttachmentInner(href, name, size)
    return node
  }

  /** Clipboard 依赖此方法把 DOM 还原成 Delta 的 attachment 值 */
  static value(domNode: HTMLElement): AttachmentValue {
    return {
      href: domNode.getAttribute('data-href') || '',
      name: domNode.getAttribute('data-name') || '',
      size: domNode.getAttribute('data-size') || '',
    }
  }
}

// 4. 响应式视频（覆盖内置 formats/video）

/**
 * 覆盖内置 Video blot：内置的 html() 实现有 bug（解构字符串得 undefined，
 * 输出 <a href="undefined"> 导致视频保存即丢失），改为输出自身 outerHTML；
 * 并补懒加载，去掉宽高改由 CSS 做响应式。
 */
class ResponsiveVideo extends VideoBlot {
  static blotName = 'video'
  static tagName = 'IFRAME'
  static className = 'ql-video'

  static create(value: string) {
    const node = super.create(value) as HTMLElement
    node.setAttribute('frameborder', '0')
    node.setAttribute('allowfullscreen', 'true')
    node.setAttribute('loading', 'lazy')
    node.setAttribute('referrerpolicy', 'no-referrer')
    return node
  }

  /** 覆盖内置的错误实现，直接输出节点自身 HTML */
  html(): string {
    return this.domNode.outerHTML
  }
}

// 5. 图片对齐（覆盖内置 formats/image）

/** 图片对齐取值，class 形如 img-align-center */
export const IMAGE_ALIGN_VALUES = ['left', 'center', 'right'] as const
export type ImageAlign = (typeof IMAGE_ALIGN_VALUES)[number]

/** 图片对齐 class 名（编辑器与前台 CSS 共用同一套命名） */
export function imageAlignClass(align: ImageAlign): string {
  return `img-align-${align}`
}

/**
 * 覆盖内置 Image blot 增加对齐。对齐用 class 而非行内 style：
 * 前台清洗白名单不放行 style（CSS 注入面），class 才能真正生效。
 * format 名用 image-align 而非 align：复用 Quill 已有的块级 align 会作用到整段文字。
 */
class AlignedImage extends ImageBlot {
  static blotName = 'image'
  static tagName = 'IMG'

  static formats(domNode: HTMLElement) {
    const formats = super.formats(domNode) || {}
    const align = IMAGE_ALIGN_VALUES.find((v) => domNode.classList.contains(imageAlignClass(v)))
    if (align) formats['image-align'] = align
    return formats
  }

  format(name: string, value: unknown) {
    if (name === 'image-align') {
      const node = this.domNode as HTMLElement
      IMAGE_ALIGN_VALUES.forEach((v) => node.classList.remove(imageAlignClass(v)))
      if (value) node.classList.add(imageAlignClass(value as ImageAlign))
      return
    }
    super.format(name, value)
  }
}

// 字体（扩展内置 font 格式的取值白名单）

/**
 * 可选字体：[取值, 中文名]。取值即 Quill ClassAttributor 的 class 后缀，
 * 真正的字体栈写在 CSS（editorStyles.ts 与前台 custom.css），取值须两处对齐。
 * 不用行内 style 方案：fontFamily 规范化会破坏 whitelist 校验与往返识别。
 */
export const FONT_OPTIONS: Array<[string, string]> = [
  ['songti', '宋体'],
  ['heiti', '黑体'],
  ['kaiti', '楷体'],
  ['fangsong', '仿宋'],
  ['mono', '等宽'],
]

/** 字体格式的取值序列（供工具栏配置与文案补写共用） */
export const FONT_KEYS: string[] = FONT_OPTIONS.map(([key]) => key)

// 注册（overwrite = true，覆盖内置 image / video）

Quill.register(HorizontalRule)
Quill.register(CalloutBlock)
Quill.register(FileAttachment)
Quill.register(ResponsiveVideo, true)
Quill.register(AlignedImage, true)

// 内置 font 是 ClassAttributor 实例，官方扩展方式就是改 whitelist 后重新注册；
// 不设白名单时 Quill 只认 serif / monospace
const FontClass: AnyBlot = Quill.import('formats/font')
FontClass.whitelist = FONT_KEYS
Quill.register(FontClass, true)

/** 需要在 QuillEditor 的 formats 白名单中列出的新增格式名（顺序无关） */
export const EXTRA_QUILL_FORMATS = ['hr', 'callout', 'attachment', 'font'] as const

// 平台视频地址解析

interface ParsedVideo {
  /** 可直接作为 iframe src 的播放器地址 */
  embedUrl: string
  /** 平台中文名，用于提示与 title */
  platform: string
  /** 视频 ID，用于卡片描述 */
  id: string
}

const BV_RE = /BV[0-9A-Za-z]{10}/
const AV_RE = /av(\d+)/i
const YOUTUBE_RE =
  /(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|live\/)|youtu\.be\/)([A-Za-z0-9_-]{6,})/i
const VIMEO_RE = /vimeo\.com\/(?:video\/)?(\d+)/i
const TENCENT_RE = /v\.qq\.com\/(?:x\/(?:cover|page)\/([A-Za-z0-9]+)\.html|.*[?&]vid=([A-Za-z0-9]+))/i

/** B 站播放器参数：关弹幕、高清优先（编辑器里只需要能播，不做复杂配置） */
function bilibiliEmbed(id: string, isAv: boolean): string {
  const key = isAv ? 'aid' : 'bvid'
  return `https://player.bilibili.com/player.html?${key}=${id}&page=1&high_quality=1&danmaku=0`
}

/**
 * 把用户粘贴的视频地址解析为可嵌入的播放器地址（B 站 / YouTube / Vimeo / 腾讯视频）。
 * 返回 null 表示无法识别，调用方应提示改用受支持的平台；b23.tv 短链需网络请求还原，不处理。
 */
export function parseVideoUrl(input: string): ParsedVideo | null {
  const raw = (input || '').trim()
  if (!raw) return null

  // B 站：允许直接粘贴 BV 号 / av 号
  const bv = raw.match(BV_RE)
  if (bv) return { embedUrl: bilibiliEmbed(bv[0], false), platform: '哔哩哔哩', id: bv[0] }

  if (/^av\d+$/i.test(raw) || /bilibili\.com/i.test(raw)) {
    const av = raw.match(AV_RE)
    if (av) return { embedUrl: bilibiliEmbed(av[1], true), platform: '哔哩哔哩', id: `av${av[1]}` }
  }

  if (/b23\.tv/i.test(raw)) return null

  const yt = raw.match(YOUTUBE_RE)
  if (yt) {
    return {
      embedUrl: `https://www.youtube.com/embed/${yt[1]}`,
      platform: 'YouTube',
      id: yt[1],
    }
  }

  const vimeo = raw.match(VIMEO_RE)
  if (vimeo) {
    return {
      embedUrl: `https://player.vimeo.com/video/${vimeo[1]}`,
      platform: 'Vimeo',
      id: vimeo[1],
    }
  }

  const tencent = raw.match(TENCENT_RE)
  if (tencent) {
    const vid = tencent[1] || tencent[2]
    return {
      embedUrl: `https://v.qq.com/txp/iframe/player.html?vid=${vid}`,
      platform: '腾讯视频',
      id: vid,
    }
  }

  return null
}

/** 前台清洗白名单允许嵌入 iframe 的域名（与 frontend/src/utils/sanitize.ts 保持一致，新增平台一起改） */
export const ALLOWED_VIDEO_HOSTS = [
  'player.bilibili.com',
  'www.youtube.com',
  'youtube.com',
  'player.vimeo.com',
  'v.qq.com',
] as const
