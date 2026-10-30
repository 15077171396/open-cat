/**
 * 富文本编辑器的**扩展**样式（表格 / 图片对齐 / 代码块 / 视频等）。
 *
 * 拆分自 `editorStyles.ts`（第五轮审查 MNT-08）：原文件 788 行里
 * 「扩展样式」与「基础静态样式」是两块互不相干的内容，改表格样式却要
 * 在 800 行里翻找。
 *
 * ⚠️ 内容块的 class 名与 `quillBlots.ts`、前台 `custom.css` **同名对齐**，
 * 改名会让前台渲染错位。
 */

export const EDITOR_EXTRA_CSS = `
  /* ===== 提示框（callout）===== */
  .quill-editor-wrapper .ql-editor .ql-callout {
    border-radius: 8px;
    padding: 0.75em 1em;
    margin: 0 0 0.9em;
    border-left: 4px solid var(--app-primary);
    background: #f0f7ff;
    color: #1e3a5f;
  }
  .quill-editor-wrapper .ql-editor .ql-callout[data-callout="info"] {
    border-left-color: var(--app-primary);
    background: #f0f7ff;
    color: #1e3a5f;
  }
  .quill-editor-wrapper .ql-editor .ql-callout[data-callout="warning"] {
    border-left-color: var(--app-warning);
    background: #fffbe6;
    color: #614700;
  }
  .quill-editor-wrapper .ql-editor .ql-callout[data-callout="success"] {
    border-left-color: var(--app-success);
    background: #f6ffed;
    color: #135200;
  }
  .quill-editor-wrapper .ql-editor .ql-callout[data-callout="danger"] {
    border-left-color: var(--app-danger);
    background: #fff1f0;
    color: #7a1416;
  }

  /* ===== 附件下载卡片 ===== */
  .quill-editor-wrapper .ql-editor .ql-file-attachment {
    margin: 0 0 0.9em;
  }
  .quill-editor-wrapper .ql-editor .ql-attachment-link {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #f8fafc;
    color: #334155;
    text-decoration: none;
    font-size: 14px;
  }
  .quill-editor-wrapper .ql-editor .ql-attachment-link:hover {
    border-color: var(--app-primary);
    background: #f0f7ff;
  }
  .quill-editor-wrapper .ql-editor .ql-attachment-link::before {
    content: '';
    flex: 0 0 auto;
    width: 18px;
    height: 18px;
    background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%231677ff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48'/%3E%3C/svg%3E") center / contain no-repeat;
  }
  .quill-editor-wrapper .ql-editor .ql-attachment-name {
    font-weight: 500;
    word-break: break-all;
  }
  .quill-editor-wrapper .ql-editor .ql-attachment-size {
    margin-left: auto;
    flex: 0 0 auto;
    color: #94a3b8;
    font-size: 12px;
  }

  /* ===== 响应式视频 ===== */
  .quill-editor-wrapper .ql-editor iframe.ql-video {
    display: block;
    width: 100%;
    height: auto;
    aspect-ratio: 16 / 9;
    border: 0;
    border-radius: 8px;
    background: #000;
    margin: 0 0 0.9em;
  }

  /* ===== 图片对齐（class 与前台一致）===== */
  .quill-editor-wrapper .ql-editor img.img-align-left {
    display: block;
    margin-left: 0;
    margin-right: auto;
  }
  .quill-editor-wrapper .ql-editor img.img-align-center {
    display: block;
    margin-left: auto;
    margin-right: auto;
  }
  .quill-editor-wrapper .ql-editor img.img-align-right {
    display: block;
    margin-left: auto;
    margin-right: 0;
  }

  /* ===== 工具栏：标题下拉中文化 =====
     snow 主题把标题级别预置成英文（Normal / Heading 1~6），与编辑器其余
     中文界面不一致。这里按 data-value 覆盖文案。
     选择器刻意带上 .ql-toolbar 提高一级特异性（snow 的规则只有 .ql-snow），
     这样无论样式表加载顺序如何都能稳定覆盖，不必赌注入顺序。
     无 data-value 即「正文」——Quill 把 select 指向末位 false 项时
     会移除 label 上的 data-value，正好落在这条默认规则上。 */
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label::before,
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-item::before {
    content: '正文';
  }
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label[data-value='1']::before,
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-item[data-value='1']::before {
    content: '一级标题';
  }
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label[data-value='2']::before,
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-item[data-value='2']::before {
    content: '二级标题';
  }
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label[data-value='3']::before,
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-item[data-value='3']::before {
    content: '三级标题';
  }
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label[data-value='4']::before,
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-item[data-value='4']::before {
    content: '四级标题';
  }
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label[data-value='5']::before,
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-item[data-value='5']::before {
    content: '五级标题';
  }
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label[data-value='6']::before,
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-item[data-value='6']::before {
    content: '六级标题';
  }

  /* ===== 字体 =====
     取值与 quillBlots.FONT_OPTIONS 一一对应；前台 frontend/src/styles/custom.css
     有同名规则，改一处必须同步改两处，否则「后台能选、前台看不到」。
     字体栈按「macOS 名 → Windows 名 → 中文名 → 通用族」排列，
     中文名兜底是因为部分 Windows 环境只按本地化名称匹配。 */
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-label::before,
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-item::before {
    content: '默认字体';
  }
  .quill-editor-wrapper .ql-editor .ql-font-songti {
    font-family: 'Songti SC', SimSun, '宋体', serif;
  }
  .quill-editor-wrapper .ql-editor .ql-font-heiti {
    font-family: 'Heiti SC', SimHei, '黑体', sans-serif;
  }
  .quill-editor-wrapper .ql-editor .ql-font-kaiti {
    font-family: 'Kaiti SC', KaiTi, '楷体', serif;
  }
  .quill-editor-wrapper .ql-editor .ql-font-fangsong {
    font-family: FangSong, STFangsong, '仿宋', serif;
  }
  .quill-editor-wrapper .ql-editor .ql-font-mono {
    font-family: 'JetBrains Mono', Consolas, 'Courier New', monospace;
  }
  /* 下拉项用自身字体渲染，展开时即可看出差别（snow 主题只对 serif/monospace 这么做） */
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-item[data-value='songti']::before {
    font-family: 'Songti SC', SimSun, '宋体', serif;
  }
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-item[data-value='heiti']::before {
    font-family: 'Heiti SC', SimHei, '黑体', sans-serif;
  }
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-item[data-value='kaiti']::before {
    font-family: 'Kaiti SC', KaiTi, '楷体', serif;
  }
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-item[data-value='fangsong']::before {
    font-family: FangSong, STFangsong, '仿宋', serif;
  }
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-item[data-value='mono']::before {
    font-family: 'JetBrains Mono', Consolas, 'Courier New', monospace;
  }

  /* ===== 工具栏：下拉宽度与「文字 ↔ 箭头」间距 =====
     这一段解决两个连在一起的现象，成因是同一个：snow 主题把下拉宽度按**英文文案**
     写死（header 98px、font 108px），而我们的文案是中文、且长度会变。

     现象一（已修）：文字与右侧箭头之间空出一大块。
       箭头是 label 的**绝对定位子元素**（quill/ui/picker.js: label.innerHTML = DropdownIcon），
       永远贴在下拉右边缘，于是「间距」= 下拉宽度 − 文字宽 − 内边距 − 箭头盒。
     现象二（本次修）：下拉盒比文案宽，多出来的宽度堆在箭头右侧成了死空间，
       再叠加 .ql-formats 的 margin-right:15px，把下一个控件推得很远，
       整条工具栏的横向间距变得忽宽忽窄。
       实测（真实浏览器，1600 宽窗口）：
         「正文」下拉 → 「默认字体」   墨迹间距 60px（盒内死空间 37px）
         「普通段落」下拉 → 「正文段落」墨迹间距 54px（死空间 31px）
       而工具栏正常的组间间距只有 24~31px、组内 10px。

     修法分两步：
       1. label 改 inline-flex，箭头从「绝对定位贴右边缘」改回普通流内元素，
          紧跟在文字后面（视觉间距 6.5px，来自 svg 自身左侧约 6px 空白）；
       2. 下拉宽度改 max-content —— **贴合当前文案**，而不是按最长文案固定。
          盒宽 = 8px 左内边距 + 文字宽 + 6.5px + 18px 箭头 + 2px 右内边距。
          改后「正文」→「默认字体」、「普通段落」→「正文段落」两处都降到 32px，
          落回工具栏既有的组间节奏（24~31px）。
          注意别改回固定宽度：固定宽度下「正文」(2 字) 与「一级标题」(4 字) 之间
          必然有一个状态留白，这就是现象二复现的原因。

     已知取舍：宽度贴合文案，所以切换标题级别 / 代码块语言时下拉盒会变宽变窄，
     右侧控件随之平移约 22~28px。这是「间距均匀」换来的代价——两者在固定宽度下
     无法兼得（死空间不是跑到箭头右边，就是跑到文字左边）。
     若哪天更在意「不抖动」，可改成「按最长文案定宽 + 内容居中」，
     让左右留白对称：代价是「正文」状态下左右各多约 14px 空白。

     只作用于这四个文字型下拉。图标型下拉（color / background / align）的箭头
     仍走 snow 的绝对定位，别把它们的布局带偏（实测仍是 position:static 的 28px 盒，
     见 make-toolbar-preview.mjs 的回归项）。 */
  .ql-snow.ql-toolbar .ql-picker.ql-header,
  .ql-snow.ql-toolbar .ql-picker.ql-font,
  .ql-snow.ql-toolbar .ql-picker.ql-callout,
  .ql-snow.ql-toolbar .ql-picker.ql-code-block {
    width: max-content;
  }
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label,
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-label,
  .ql-snow.ql-toolbar .ql-picker.ql-callout .ql-picker-label,
  .ql-snow.ql-toolbar .ql-picker.ql-code-block .ql-picker-label {
    display: inline-flex;
    align-items: center;
  }
  /* 文字不参与收缩：flex 项默认 flex-shrink:1，文案超出时会被压得换行 */
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label::before,
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-label::before,
  .ql-snow.ql-toolbar .ql-picker.ql-callout .ql-picker-label::before,
  .ql-snow.ql-toolbar .ql-picker.ql-code-block .ql-picker-label::before {
    flex: 0 0 auto;
  }
  /* 箭头从「绝对定位贴右边缘」改为普通流内元素，紧跟在文字后面。
     18px 是 snow 原尺寸，保持不变以免笔画变细（可见箭头只占盒子中间 6~12px）。 */
  .ql-snow.ql-toolbar .ql-picker.ql-header .ql-picker-label svg,
  .ql-snow.ql-toolbar .ql-picker.ql-font .ql-picker-label svg,
  .ql-snow.ql-toolbar .ql-picker.ql-callout .ql-picker-label svg,
  .ql-snow.ql-toolbar .ql-picker.ql-code-block .ql-picker-label svg {
    position: static;
    margin: 0;
    width: 18px;
    height: 18px;
    flex: 0 0 auto;
  }

  /* ===== 工具栏：提示框下拉 =====
     Quill 只对内置 picker（header/font/size）预置了 ::before 文案，
     自定义 picker 必须自行补上，否则标签是空白的 */
  .ql-snow .ql-picker.ql-callout .ql-picker-label::before,
  .ql-snow .ql-picker.ql-callout .ql-picker-item::before {
    content: '正文段落';
  }
  .ql-snow .ql-picker.ql-callout .ql-picker-label[data-label]:not([data-label=''])::before,
  .ql-snow .ql-picker.ql-callout .ql-picker-item[data-label]:not([data-label=''])::before {
    content: attr(data-label);
  }

  /* ===== 工具栏：代码块语言下拉 =====
     与 callout 同款处理。光标不在代码块内时，Quill 会把工具栏的 select
     指到带 selected 属性的那一项（即「普通段落」），因此这里的默认文案
     与末位选项一致，视觉上不会出现空白标签 */
  .ql-snow .ql-picker.ql-code-block .ql-picker-label::before,
  .ql-snow .ql-picker.ql-code-block .ql-picker-item::before {
    content: '普通段落';
  }
  .ql-snow .ql-picker.ql-code-block .ql-picker-label[data-label]:not([data-label=''])::before,
  .ql-snow .ql-picker.ql-code-block .ql-picker-item[data-label]:not([data-label=''])::before {
    content: attr(data-label);
  }

  /* ===== 状态栏（字数统计）===== */
  .quill-statusbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    padding: 6px 12px;
    border-top: 1px solid var(--app-border-light);
    background: #fafafa;
    font-size: 12px;
    color: #94a3b8;
    /* 状态栏是编辑器框内最后一个元素：不跟随外框圆角时，灰底会盖住底部两个圆角 */
    border-radius: 0 0 7px 7px;
  }
  .quill-statusbar b {
    font-weight: 600;
    color: #475569;
  }
  .quill-statusbar-spacer {
    flex: 1;
  }
  .quill-statusbar-hint {
    cursor: pointer;
    color: var(--app-primary);
    background: none;
    border: none;
    font: inherit;
    padding: 0;
  }
  .quill-statusbar-hint:hover {
    text-decoration: underline;
  }

  /* ===== 查找替换面板 ===== */
  .quill-find-panel {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid #e2e8f0;
    background: #f8fafc;
    font-size: 13px;
  }
  .quill-find-panel input {
    height: 28px;
    padding: 0 8px;
    border: 1px solid #d9d9d9;
    border-radius: 4px;
    font-size: 13px;
    outline: none;
    min-width: 140px;
  }
  .quill-find-panel input:focus {
    border-color: var(--app-primary);
  }
  .quill-find-panel button {
    height: 28px;
    padding: 0 10px;
    border: 1px solid #d9d9d9;
    border-radius: 4px;
    background: #fff;
    color: #334155;
    font-size: 13px;
    cursor: pointer;
  }
  .quill-find-panel button:hover {
    border-color: var(--app-primary);
    color: var(--app-primary);
  }
  .quill-find-count {
    color: #94a3b8;
    min-width: 62px;
  }
  .quill-find-close {
    margin-left: auto;
  }

  /* ===== HTML 源码模式 =====
     源码模式下不卸载 ReactQuill（卸载会销毁 Quill 实例、丢失编辑内容），
     只把工具栏与正文区隐藏，用同容器内的 textarea 顶替 */
  .quill-editor-wrapper.quill-source-mode .ql-toolbar,
  .quill-editor-wrapper.quill-source-mode .ql-container {
    display: none;
  }
  .quill-source-textarea {
    flex: 1;
    min-height: 0;
    width: 100%;
    box-sizing: border-box;
    padding: 12px 15px;
    border: none;
    outline: none;
    resize: none;
    font-family: Consolas, Monaco, 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.7;
    color: #334155;
    background: #fbfdff;
    tab-size: 2;
  }

  /* ===== 预览 ===== */
  .quill-preview-note {
    margin-top: 14px;
    padding: 8px 12px;
    border-radius: 6px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    font-size: 12px;
    line-height: 1.7;
    color: #94a3b8;
  }
  .quill-preview-body {
    font-size: 15px;
    line-height: 1.8;
    color: #334155;
  }
  .quill-preview-body img {
    max-width: 100%;
    height: auto;
    border-radius: 8px;
  }
  .quill-preview-body img.img-align-left { display: block; margin-right: auto; }
  .quill-preview-body img.img-align-center { display: block; margin-left: auto; margin-right: auto; }
  .quill-preview-body img.img-align-right { display: block; margin-left: auto; }
  .quill-preview-body iframe.ql-video {
    display: block;
    width: 100%;
    aspect-ratio: 16 / 9;
    border: 0;
    border-radius: 8px;
    background: #000;
  }
  .quill-preview-body pre {
    background: #1e293b;
    color: #f8fafc;
    padding: 0.9em 1em;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 0.875em;
  }
  .quill-preview-body pre code {
    background: transparent;
    color: inherit;
    padding: 0;
  }
  .quill-preview-body code {
    background: #f1f5f9;
    padding: 0.15em 0.4em;
    border-radius: 4px;
    font-size: 0.875em;
  }
  .quill-preview-body blockquote {
    border-left: 4px solid #d1d5db;
    padding-left: 1em;
    color: #6b7280;
    margin: 0 0 0.9em;
  }
  .quill-preview-body table {
    width: 100%;
    border-collapse: collapse;
  }
  .quill-preview-body th,
  .quill-preview-body td {
    border: 1px solid #e2e8f0;
    padding: 0.5em 0.75em;
  }
  .quill-preview-body th {
    background: #f8fafc;
  }
  .quill-preview-body .ql-callout {
    border-radius: 8px;
    padding: 0.75em 1em;
    margin: 0 0 0.9em;
    border-left: 4px solid var(--app-primary);
    background: #f0f7ff;
    color: #1e3a5f;
  }
  .quill-preview-body .ql-callout[data-callout="warning"] { border-left-color: var(--app-warning); background: #fffbe6; color: #614700; }
  .quill-preview-body .ql-callout[data-callout="success"] { border-left-color: var(--app-success); background: #f6ffed; color: #135200; }
  .quill-preview-body .ql-callout[data-callout="danger"] { border-left-color: var(--app-danger); background: #fff1f0; color: #7a1416; }
  .quill-preview-body .ql-attachment-link {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #f8fafc;
    text-decoration: none;
    color: #334155;
  }
  .quill-preview-body .ql-attachment-size { margin-left: auto; color: #94a3b8; font-size: 12px; }

  /* ===== 快捷键帮助 ===== */
  .quill-shortcut-group {
    margin-bottom: 18px;
  }
  .quill-shortcut-group h4 {
    margin: 0 0 8px;
    font-size: 13px;
    color: #64748b;
    font-weight: 600;
  }
  .quill-shortcut-row {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 4px 0;
    font-size: 13px;
    color: #334155;
  }
  .quill-shortcut-key {
    flex: 0 0 168px;
    font-family: Consolas, Monaco, 'Courier New', monospace;
    font-size: 12px;
    color: #475569;
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    padding: 2px 6px;
    text-align: center;
  }
`

/**
 * 静态样式表（与组件渲染无关的 CSS 规则）
 * 通过类名管理样式可：1) 避免内联 style 对象每次渲染重建；
 * 2) 用 CSS :hover 替代 JS 事件处理，提升渲染性能；3) 便于外部覆盖。
 */
