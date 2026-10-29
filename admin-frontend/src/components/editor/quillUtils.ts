/** 富文本编辑器的常量与纯工具函数（与组件状态、生命周期无关，可离线校验） */

import DOMPurify from 'dompurify'
import type { Quill } from 'react-quill-new'

import { ALLOWED_VIDEO_HOSTS } from './quillBlots'

// 快捷键表引用的键位字面量，单源于 toolbarConfig（避免同一键位写两遍而漂移）
import { STRUCTURE_SHORTCUT_LABELS } from './toolbarConfig'

/** 目录提取防抖延迟（ms）：合并快速输入时的多次 DOM 查询，平衡响应性与性能 */
export const TOC_DEBOUNCE_MS = 50

/** 编辑器正文区域默认高度 */
export const DEFAULT_HEIGHT = 'calc(100vh - 360px)'
/** 编辑器正文区域默认最小高度 */
export const DEFAULT_MIN_HEIGHT = '400px'

/** 图片拖拽调整的最小宽度（px），过小难以辨认且容易误操作 */
export const IMAGE_RESIZE_MIN_WIDTH = 80

/**
 * DOMPurify 消毒配置（纵深防御）
 * DOMPurify 默认已清除 script / on* 事件等危险载荷，此处显式声明
 * FORBID 列表作为可审计的配置来源；不影响 Quill 依赖的 span[style]、img[src] 等合法标签。
 */
export const DOMPURIFY_CONFIG = {
  FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form'],
  FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover'],
  ALLOW_DATA_ATTR: true,
}

/**
 * 预览用的消毒配置：在通用配置基础上放行 iframe 与视频所需属性。
 * 预览渲染的是当前管理员自己正在编辑的内容（同一信任域），
 * 真正的 XSS 边界在前台 RichText（那里的白名单要严格得多）。
 */
export const PREVIEW_SANITIZE_CONFIG = {
  ...DOMPURIFY_CONFIG,
  FORBID_TAGS: ['script', 'object', 'embed', 'form'],
  ADD_TAGS: ['iframe'],
  ADD_ATTR: ['allowfullscreen', 'frameborder', 'referrerpolicy', 'loading', 'download', 'colspan', 'rowspan'],
}

/**
 * 校验 iframe 地址：必须是 https 绝对地址、且域名在播放器白名单内
 * （复用 quillBlots.ALLOWED_VIDEO_HOSTS）。相对地址可借当前站点协议绕过 https 校验，不接受。
 */
function isAllowedVideoSrc(src: string): boolean {
  try {
    const url = new URL(src)
    return (
      url.protocol === 'https:' &&
      (ALLOWED_VIDEO_HOSTS as readonly string[]).includes(url.hostname)
    )
  } catch {
    return false
  }
}

/**
 * 预览专用消毒器（独立实例，addHook 会污染其它模块）。
 * 预览放行了 iframe 且源码模式可直插任意 iframe，预览渲染在 /admin/ 可信源下，
 * 必须校验 iframe 域名，否则等于把本站展示位交给任意页面。
 */
const previewSanitizer = DOMPurify(window)
previewSanitizer.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName !== 'IFRAME') return
  const src = node.getAttribute('src') || ''
  if (!isAllowedVideoSrc(src)) node.remove()
})

/** 消毒预览 HTML：放行白名单内的视频 iframe，其余 iframe **整块移除**（与前台策略一致） */
export function sanitizePreviewHtml(html: string): string {
  return previewSanitizer.sanitize(html, PREVIEW_SANITIZE_CONFIG)
}

/**
 * 把紧凑键位（`Ctrl+Alt+1~6`）展开成帮助面板用的易读写法（`Ctrl + Alt + 1 ~ 6`）。
 * 键位本身只维护在 STRUCTURE_SHORTCUT_LABELS 一份，这里只做排版转换。
 */
export const readableKeys = (keys: string): string =>
  keys.replace(/\+/g, ' + ').replace(/~/g, ' ~ ')

/**
 * 快捷键帮助内容（分组展示）。
 * 每一条都必须是真实注册过的绑定；不要写入浏览器保留键（拦不住，写了等于骗用户）。
 */
export const SHORTCUT_GROUPS: Array<{ title: string; items: Array<[string, string]> }> = [
  {
    title: '文字格式',
    items: [
      ['Ctrl + B', '加粗'],
      ['Ctrl + I', '斜体'],
      ['Ctrl + U', '下划线'],
      [readableKeys(STRUCTURE_SHORTCUT_LABELS.link), '插入 / 编辑链接'],
      [readableKeys(STRUCTURE_SHORTCUT_LABELS.inlineCode), '行内代码'],
    ],
  },
  {
    title: '段落与结构',
    items: [
      [readableKeys(STRUCTURE_SHORTCUT_LABELS.headerNormal), '正文'],
      [readableKeys(STRUCTURE_SHORTCUT_LABELS.header), '一级 ~ 六级标题'],
      [readableKeys(STRUCTURE_SHORTCUT_LABELS.listOrdered), '有序列表'],
      [readableKeys(STRUCTURE_SHORTCUT_LABELS.listBullet), '无序列表'],
      [readableKeys(STRUCTURE_SHORTCUT_LABELS.blockquote), '引用块'],
      [readableKeys(STRUCTURE_SHORTCUT_LABELS.codeBlock), '代码块'],
      ['Tab / Shift + Tab', '增加 / 减少缩进'],
    ],
  },
  {
    title: '编辑效率',
    items: [
      ['Ctrl + Z / Ctrl + Y', '撤销 / 重做'],
      ['Ctrl + F', '查找与替换'],
      ['Ctrl + V', '粘贴（图片自动上传到 COS）'],
      ['拖拽图片文件', '直接插入并上传'],
      ['Esc', '退出全屏 / 关闭当前面板'],
    ],
  },
]

/** 编辑器插入类功能的快捷键说明（供工具栏提示复用） */
export const VIDEO_HINT =
  '支持哔哩哔哩（BV 号 / av 号 / 完整链接）、YouTube、Vimeo、腾讯视频。' +
  'b23.tv 短链请先展开为完整链接再粘贴。'

/** 将数值型尺寸归一化为合法 CSS 长度串 */
export function resolveSize(v: string | number | undefined, fallback: string): string {
  if (v === undefined || v === null) return fallback
  return typeof v === 'number' ? `${v}px` : v
}

/** 图片插入占位文案：上传期间显示，完成后删除 */
export const INSERT_PLACEHOLDER = '图片上传中...'

/** 上传结束后按文本重新定位占位符，返回其索引；不存在返回 -1（此时调用方只插图不删除） */
export function locateUploadPlaceholder(text: string): number {
  return text.indexOf(INSERT_PLACEHOLDER)
}

/** 从 FileList / files 中筛选图片文件（粘贴、拖拽共用） */
export function extractImageFiles(files: FileList | null | undefined): File[] {
  return Array.from(files || []).filter((f) => f.type.startsWith('image/'))
}

/** 字节数格式化为人类可读文本（附件卡片展示用） */
export function formatBytes(bytes: number): string {
  if (!bytes) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${i === 0 || value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[i]}`
}

interface DocStats {
  /** 不含空白字符的字符数 */
  chars: number
  /** 英文单词数 */
  words: number
  /** 段落 / 标题 / 列表项等块级元素数量 */
  blocks: number
  /** 预计阅读时长（分钟） */
  minutes: number
}

/**
 * 统计正文规模。直接解析 HTML 而非 editor.getText()：后者会丢弃 embed（图片/视频/附件）。
 */
export function computeStats(html: string): DocStats {
  if (!html) return { chars: 0, words: 0, blocks: 0, minutes: 0 }
  const doc = new DOMParser().parseFromString(html, 'text/html')
  doc.querySelectorAll('script, style').forEach((el) => el.remove())
  const text = (doc.body.textContent || '').replace(/\u00a0/g, ' ')
  const chars = text.replace(/\s/g, '').length
  const cjk = (text.match(/[\u3400-\u9fff]/g) || []).length
  const words = (text.match(/[A-Za-z0-9][A-Za-z0-9'’_-]*/g) || []).length
  const blocks = doc.body.querySelectorAll(
    'p, h1, h2, h3, h4, h5, h6, li, blockquote, pre, .ql-callout',
  ).length
  // 中文按 400 字/分钟、英文按 400 词/分钟估算，向上取整且至少 1 分钟
  const minutes = chars === 0 ? 0 : Math.max(1, Math.ceil((cjk + words) / 400))
  return { chars, words, blocks, minutes }
}

/** 单个查找命中项（index / length 均为 Quill 文档索引） */
export interface FindMatch {
  index: number
  length: number
}

/**
 * 在编辑器文档中查找全部命中项。
 * 逐行扫描而非全局 getText()：后者丢弃 embed 会让字符索引整体错位，替换会改错位置。
 */
export function collectMatches(quill: Quill, needle: string, caseSensitive: boolean): FindMatch[] {
  if (!needle) return []
  const results: FindMatch[] = []
  const target = caseSensitive ? needle : needle.toLowerCase()
  let lines: ReturnType<Quill['getLines']> = []
  try {
    lines = quill.getLines(0, quill.getLength())
  } catch {
    return results
  }
  for (const line of lines) {
    const text = (line.domNode as HTMLElement).textContent || ''
    const haystack = caseSensitive ? text : text.toLowerCase()
    const base = quill.getIndex(line)
    let from = 0
    for (;;) {
      const at = haystack.indexOf(target, from)
      if (at < 0) break
      results.push({ index: base + at, length: needle.length })
      from = at + needle.length
    }
  }
  return results
}

/**
 * base64 图片标签匹配：处理从网页/Word 复制时图片以 data:URL 存在于 text/html 的情况
 * （仅拦截粘贴事件捕获不到，必须解析 HTML）。
 */
export const BASE64_IMG_REGEX = /<img[^>]+src="data:image\/([a-zA-Z+]+);base64,([^"]+)"[^>]*>/gi

/** 用上传后的远程 URL 替换 HTML 中的所有 base64 图片标签 */
export async function replaceBase64ImagesWithUrls(html: string, uploadFn: (file: File) => Promise<string>): Promise<string> {
  const matches: { fullMatch: string; file: File }[] = []
  BASE64_IMG_REGEX.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = BASE64_IMG_REGEX.exec(html)) !== null) {
    const [, ext, b64] = match
    try {
      const mime = `image/${ext.toLowerCase()}`
      const binary = atob(b64)
      const bytes = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
      const file = new File([bytes], `paste_${Date.now()}_${matches.length}.${ext.toLowerCase().replace('+', '')}`, { type: mime })
      matches.push({ fullMatch: match[0], file })
    } catch (err) {
      console.warn('base64 图片转换失败:', err)
    }
  }

  if (matches.length === 0) return html

  // 串行上传，保证替换顺序与原 HTML 一致
  let result = html
  for (const { fullMatch, file } of matches) {
    try {
      const url = await uploadFn(file)
      // 用远程 URL 构造新的 img 标签（保留 src 之外属性较复杂，这里直接重建）
      result = result.replace(fullMatch, `<img src="${url}" alt="" />`)
    } catch (err) {
      console.warn('粘贴图片上传失败，跳过替换:', err)
    }
  }
  return result
}
