/** 富文本编辑器的工具栏 / 格式白名单配置（纯数据，无 React 依赖，可离线校验） */

import type Quill from 'quill'

import { CALLOUT_LABELS, CALLOUT_TYPES, EXTRA_QUILL_FORMATS, FONT_KEYS, FONT_OPTIONS } from './quillBlots'

/**
 * 代码块语言下拉。取值须与前台 highlight.js 的注册语言名对齐
 * （frontend/src/utils/codeHighlight.ts），新增语言时两处一起改。
 */
export const CODE_BLOCK_LANGUAGES: Array<[string, string]> = [
  ['plain', '纯文本'],
  ['bash', 'Shell / Bash'],
  ['javascript', 'JavaScript'],
  ['typescript', 'TypeScript'],
  ['json', 'JSON'],
  ['python', 'Python'],
  ['java', 'Java'],
  ['go', 'Go'],
  ['rust', 'Rust'],
  ['cpp', 'C / C++'],
  ['csharp', 'C#'],
  ['php', 'PHP'],
  ['ruby', 'Ruby'],
  ['sql', 'SQL'],
  ['yaml', 'YAML'],
  ['xml', 'HTML / XML'],
  ['css', 'CSS'],
  ['markdown', 'Markdown'],
  ['diff', 'Diff'],
]

/** 代码块语言下拉的取值序列（供工具栏配置与文案补写共用） */
const CODE_BLOCK_LANGUAGE_KEYS = CODE_BLOCK_LANGUAGES.map(([key]) => key)

/** 段落结构类快捷键的键位文案（单一事实来源：键盘绑定、帮助面板、按钮提示三处共用） */
export const STRUCTURE_SHORTCUT_LABELS = {
  headerNormal: 'Ctrl+Alt+0',
  header: 'Ctrl+Alt+1~6',
  listOrdered: 'Ctrl+Shift+7',
  listBullet: 'Ctrl+Shift+8',
  blockquote: 'Ctrl+Shift+9',
  inlineCode: 'Ctrl+Alt+I',
  codeBlock: 'Ctrl+Alt+B',
  link: 'Ctrl+Alt+L',
  redo: 'Ctrl+Y',
} as const

/**
 * Quill 工具栏容器配置（handlers 在组件内注入）。
 * `{ callout: [...] }` 与 `{ 'code-block': [...] }` 是自定义 select，末位 false 为取消项；
 * code-block 无需自定义 handler，Quill 对未注册 handler 的格式走通用 format 分支。
 */
export const QUILL_TOOLBAR_CONTAINER: (string | Record<string, unknown>)[][] = [
  [{ header: [1, 2, 3, 4, 5, 6, false] }],
  // 字体取值必须显式列出：addSelect 对空数组不生成任何 <option>，下拉会是空的
  [{ font: [...FONT_KEYS, false] }],
  ['bold', 'italic', 'underline', 'strike', 'code', { script: 'sub' }, { script: 'super' }],
  [{ color: [] }, { background: [] }],
  [{ list: 'ordered' }, { list: 'bullet' }, { list: 'check' }],
  [{ indent: '-1' }, { indent: '+1' }],
  [{ align: [] }],
  ['blockquote', 'code-block', { 'code-block': [...CODE_BLOCK_LANGUAGE_KEYS, false] }],
  [{ callout: [...CALLOUT_TYPES, false] }],
  ['link', 'image', 'search-image', 'video', 'attachment', 'table-better', 'hr'],
  ['undo', 'redo'],
  ['find', 'source', 'preview', 'help', 'fullscreen', 'clean', 'ai'],
]

/**
 * 提示框下拉的选项文案，顺序与 callout 取值一一对应。
 * Quill 的 addSelect 只生成空 <option>，必须由组件挂载后按此表补写。
 */
const CALLOUT_OPTION_LABELS: string[] = [
  ...CALLOUT_TYPES.map((type) => CALLOUT_LABELS[type]),
  '正文段落',
]

/** 所有下拉的选项文案表：[格式名, 每项文案]，顺序须与工具栏取值数组严格一一对应 */
const SELECT_OPTION_LABELS: Array<[string, string[]]> = [
  ['font', [...FONT_OPTIONS.map(([, label]) => label), '默认字体']],
  ['callout', CALLOUT_OPTION_LABELS],
  ['code-block', [...CODE_BLOCK_LANGUAGES.map(([, label]) => label), '普通段落']],
]

/**
 * 把下拉的选项文案补写进 Quill 生成的工具栏 DOM。
 * Quill 的 addSelect 只造出没有文本的 <option>，而 Picker 构造时就读走文本，
 * 因此 select.options、.ql-picker-item、.ql-picker-label 三处都要补。
 */
export function applySelectOptionLabels(container: HTMLElement): void {
  SELECT_OPTION_LABELS.forEach(([format, labels]) => {
    const select = container.querySelector<HTMLSelectElement>(`select.ql-${format}`)
    if (!select) return

    Array.from(select.options).forEach((option, index) => {
      if (labels[index]) option.textContent = labels[index]
    })

    const picker = container.querySelector(`.ql-picker.ql-${format}`)
    if (!picker) return

    picker.querySelectorAll('.ql-picker-item').forEach((item, index) => {
      if (labels[index]) item.setAttribute('data-label', labels[index])
    })

    const label = picker.querySelector('.ql-picker-label')
    if (label) {
      // 光标所在位置没有该格式时，Quill 会把 selectedIndex 置为 -1，
      // 并把 select 指向带 selected 属性的末位项（即「默认 / 正文」）
      const selected = select.selectedIndex
      label.setAttribute('data-label', labels[selected >= 0 ? selected : labels.length - 1])
    }
  })
}

// 段落结构类快捷键

/**
 * Quill keyboard 绑定结构（只声明本项目用到的字段）。
 * key 须与 evt.key 严格相等（数字键写 '1' 而非 1）；未声明的修饰键视为必须为假；
 * handler 返回 true 不阻止默认行为，返回其它值则 preventDefault。
 */
interface BlockShortcutBinding {
  key: string | number
  ctrlKey?: boolean
  altKey?: boolean
  shiftKey?: boolean
  handler: (this: { quill: Quill }, range: { index: number; length: number }) => boolean | void
}

const CTRL_ALT = { ctrlKey: true, altKey: true }
const CTRL_SHIFT = { ctrlKey: true, shiftKey: true }

/** 生成「把光标所在行设为某个块级格式」的快捷键绑定 */
function makeBlockFormatBinding(
  key: string | number,
  format: string,
  value: unknown,
  modifiers: { ctrlKey?: boolean; altKey?: boolean; shiftKey?: boolean },
): BlockShortcutBinding {
  return {
    key,
    ...modifiers,
    handler(this: { quill: Quill }) {
      this.quill.format(format, value, 'user')
      return false
    },
  }
}

/**
 * 段落结构类快捷键的键盘绑定（Quill 2 未内置这些快捷键，须自行注册）。
 * 键位约束：避开浏览器保留键（Ctrl+Shift+B/C、Ctrl+K）；标题沿用 Google Docs
 * 的 Ctrl+Alt+数字；Ctrl+Shift+数字时 evt.key 是符号而非数字，须用 keyCode 55/56/57。
 */
export const STRUCTURE_KEYBOARD_BINDINGS: Record<string, BlockShortcutBinding> = {
  // 标题：Ctrl + Alt + 0 ~ 6
  'header normal': makeBlockFormatBinding('0', 'header', false, CTRL_ALT),
  'header 1': makeBlockFormatBinding('1', 'header', 1, CTRL_ALT),
  'header 2': makeBlockFormatBinding('2', 'header', 2, CTRL_ALT),
  'header 3': makeBlockFormatBinding('3', 'header', 3, CTRL_ALT),
  'header 4': makeBlockFormatBinding('4', 'header', 4, CTRL_ALT),
  'header 5': makeBlockFormatBinding('5', 'header', 5, CTRL_ALT),
  'header 6': makeBlockFormatBinding('6', 'header', 6, CTRL_ALT),

  // 列表与引用：Ctrl + Shift + 7 / 8 / 9（keyCode 匹配，见上方第 3 条）
  'list ordered': makeBlockFormatBinding(55, 'list', 'ordered', CTRL_SHIFT),
  'list bullet': makeBlockFormatBinding(56, 'list', 'bullet', CTRL_SHIFT),
  blockquote: makeBlockFormatBinding(57, 'blockquote', true, CTRL_SHIFT),

  // 行内代码 / 代码块：Ctrl + Alt + I / B
  'inline code': makeBlockFormatBinding('i', 'code', true, CTRL_ALT),
  'code block': makeBlockFormatBinding('b', 'code-block', 'plain', CTRL_ALT),

  // 重做：Quill 默认只绑了 Ctrl+Shift+Z，Windows 用户更习惯 Ctrl+Y，补一个
  redo: {
    key: 'y',
    ctrlKey: true,
    handler(this: { quill: Quill }) {
      const history = this.quill.getModule('history') as { redo?: () => void } | undefined
      history?.redo?.()
      return false
    },
  },

  // 插入/编辑链接：直接触发工具栏按钮，保证与鼠标点击行为一致
  link: {
    key: 'l',
    ctrlKey: true,
    altKey: true,
    handler(this: { quill: Quill }) {
      const toolbar = this.quill.getModule('toolbar') as { container?: HTMLElement } | undefined
      toolbar?.container?.querySelector('.ql-link')?.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      )
      return false
    },
  },
}


/** 全屏按钮图标（展开 / 收起两态） */
export const FULLSCREEN_ENTER_SVG = '<svg viewBox="0 0 18 18"><path class="ql-stroke" fill="none" d="M3 6V3h3M15 6V3h-3M3 12v3h3M15 12v3h-3"/></svg>'
export const FULLSCREEN_EXIT_SVG = '<svg viewBox="0 0 18 18"><path class="ql-stroke" fill="none" d="M6 3v3H3M12 3v3h3M6 15v-3H3M12 15v-3h3"/></svg>'

/**
 * 自定义按钮图标：Quill 主题只为内置格式提供图标，自定义格式的按钮 innerHTML 为空须自行注入。
 * 统一使用 ql-stroke / ql-fill 类，配色由主题 CSS 接管。
 */
export const CUSTOM_ICONS: Record<string, string> = {
  undo: '<svg viewBox="0 0 18 18"><path class="ql-stroke" fill="none" d="M3.5 8h7a3.75 3.75 0 0 1 0 7.5H7M3.5 8l3-3M3.5 8l3 3"/></svg>',
  redo: '<svg viewBox="0 0 18 18"><path class="ql-stroke" fill="none" d="M14.5 8h-7a3.75 3.75 0 0 0 0 7.5H11M14.5 8l-3-3M14.5 8l-3 3"/></svg>',
  hr: '<svg viewBox="0 0 18 18"><rect class="ql-fill" x="2" y="8.25" width="14" height="1.5" rx="0.75"/></svg>',
  attachment: '<svg viewBox="0 0 18 18"><path class="ql-stroke" fill="none" d="M15.2 8.4l-5.9 5.9a3.9 3.9 0 0 1-5.6-5.5l6.2-6.2a2.6 2.6 0 0 1 3.7 3.7l-6.2 6.2a1.3 1.3 0 0 1-1.9-1.8l5.6-5.6"/></svg>',
  find: '<svg viewBox="0 0 18 18"><circle class="ql-stroke" fill="none" cx="8" cy="8" r="4.4"/><line class="ql-stroke" x1="11.4" y1="11.4" x2="15" y2="15"/></svg>',
  source: '<svg viewBox="0 0 18 18"><polyline class="ql-stroke" fill="none" points="6.4 5 3 9 6.4 13"/><polyline class="ql-stroke" fill="none" points="11.6 5 15 9 11.6 13"/></svg>',
  preview: '<svg viewBox="0 0 18 18"><path class="ql-stroke" fill="none" d="M1.6 9S4.6 4.6 9 4.6 16.4 9 16.4 9 13.4 13.4 9 13.4 1.6 9 1.6 9z"/><circle class="ql-fill" cx="9" cy="9" r="2"/></svg>',
  help: '<svg viewBox="0 0 18 18"><circle class="ql-stroke" fill="none" cx="9" cy="9" r="6.4"/><path class="ql-stroke" fill="none" d="M7.1 7.1a1.95 1.95 0 1 1 2.6 1.85c-.4.14-.6.5-.6.9v.35"/><rect class="ql-fill" x="8.3" y="12.2" width="1.4" height="1.4" rx="0.7"/></svg>',
  ai: '<svg viewBox="0 0 18 18"><path class="ql-stroke" fill="none" d="M9 2.5l1.4 3.6L14 7.5l-3.6 1.4L9 12.5 7.6 8.9 4 7.5l3.6-1.4L9 2.5z"/><path class="ql-stroke" fill="none" d="M13.5 11.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z"/></svg>',
  'search-image': '<svg viewBox="0 0 18 18"><circle class="ql-stroke" fill="none" cx="7.4" cy="7.4" r="4.2"/><line class="ql-stroke" x1="10.6" y1="10.6" x2="15" y2="15"/><path class="ql-stroke" fill="none" d="M4.8 8.1l1.7-1.8 1.4 1.4 1.7-1.7"/></svg>',
}

/**
 * 工具栏按钮悬停提示（含快捷键），按 CSS 选择器匹配 Quill 生成的元素。
 * 选择器口径：下拉类须挂在 .ql-picker-label 上（写在 select 上不可见）；
 * 列表按钮取值在 value 属性上（写 data-type 会失配）。
 * ⚠️ 提示里的键位一律取自 STRUCTURE_SHORTCUT_LABELS，不要在此硬编码。
 */
export const TOOLBAR_TITLES: Array<[string, string]> = [
  ['.ql-picker.ql-header .ql-picker-label', `标题级别 (${STRUCTURE_SHORTCUT_LABELS.header})`],
  ['.ql-picker.ql-font .ql-picker-label', '字体（作用于选中的文字）'],
  ['button.ql-bold', '加粗 (Ctrl+B)'],
  ['button.ql-italic', '斜体 (Ctrl+I)'],
  ['button.ql-underline', '下划线 (Ctrl+U)'],
  ['button.ql-strike', '删除线'],
  ['button.ql-code', `行内代码 (${STRUCTURE_SHORTCUT_LABELS.inlineCode})`],
  ['button.ql-script[value="sub"]', '下标'],
  ['button.ql-script[value="super"]', '上标'],
  ['.ql-picker.ql-color .ql-picker-label', '文字颜色'],
  ['.ql-picker.ql-background .ql-picker-label', '背景色'],
  ['button.ql-list[value="ordered"]', `有序列表 (${STRUCTURE_SHORTCUT_LABELS.listOrdered})`],
  ['button.ql-list[value="bullet"]', `无序列表 (${STRUCTURE_SHORTCUT_LABELS.listBullet})`],
  ['button.ql-list[value="check"]', '任务清单（可勾选）'],
  ['button.ql-indent[value="-1"]', '减少缩进'],
  ['button.ql-indent[value="+1"]', '增加缩进'],
  ['.ql-picker.ql-align .ql-picker-label', '对齐方式'],
  ['button.ql-search-image', '搜索图片（图库）'],
  ['button.ql-blockquote', `引用块 (${STRUCTURE_SHORTCUT_LABELS.blockquote})`],
  ['button.ql-code-block', `代码块 (${STRUCTURE_SHORTCUT_LABELS.codeBlock})`],
  ['.ql-picker.ql-code-block .ql-picker-label', '代码块语言（选「普通段落」可还原为正文）'],
  ['.ql-picker.ql-callout .ql-picker-label', '提示框（信息 / 注意 / 成功 / 风险）'],
  ['button.ql-link', `插入链接 (${STRUCTURE_SHORTCUT_LABELS.link})`],
  ['button.ql-image', '插入图片（也可直接粘贴或拖拽）'],
  ['button.ql-video', '插入在线视频（B 站 / YouTube / Vimeo / 腾讯视频）'],
  ['button.ql-attachment', '插入附件下载卡片'],
  ['button.ql-table-better', '插入表格'],
  ['button.ql-hr', '插入水平分割线'],
  ['button.ql-undo', '撤销 (Ctrl+Z)'],
  ['button.ql-redo', `重做 (${STRUCTURE_SHORTCUT_LABELS.redo})`],
  ['button.ql-find', '查找替换 (Ctrl+F)'],
  ['button.ql-source', 'HTML 源码模式'],
  ['button.ql-preview', '预览渲染效果'],
  ['button.ql-help', '快捷键与功能说明'],
  ['button.ql-clean', '清除格式'],
  ['button.ql-ai', 'AI 写作助手'],
]

/**
 * 允许的格式白名单，需与工具栏配置匹配。Quill 指定 formats 后会构建受限注册表：
 * 必须包含 quill-table-better 的全部表格 blot 与自定义 blot；但不能列入
 * image 的 width/height（非独立 blot，列入会报注册错误）。
 * 扩展格式名集中在 quillBlots.EXTRA_QUILL_FORMATS 维护。
 */
export const QUILL_FORMATS = [
  'header', 'bold', 'italic', 'underline', 'strike',
  'code', 'script',
  'color', 'background',
  'list', 'align', 'indent', 'direction',
  'blockquote', 'code-block',
  'link', 'image', 'video',
  ...EXTRA_QUILL_FORMATS,
  // 表格（quill-table-better）
  'table-cell-block', 'table-th-block',
  'table-cell', 'table-th',
  'table-row', 'table-th-row',
  'table-body', 'table-thead',
  'table-temporary', 'table-container',
  'table-col', 'table-colgroup',
  'table-header', 'table-list', 'table-list-container',
]
