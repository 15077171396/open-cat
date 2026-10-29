/**
 * 富文本编辑器的**基础静态**样式（与组件渲染无关的 CSS 规则）。
 *
 * 用类名管理样式而不是内联 style 的三个理由：
 * 1) 避免内联 style 对象每次渲染重建；
 * 2) 用 CSS `:hover` 替代 JS 事件处理；
 * 3) 便于外部（前台 `custom.css`）覆盖。
 */

export const STATIC_EDITOR_CSS = `
  .quill-root-layout {
    display: flex;
    gap: 16px;
    align-items: stretch;
  }
  .quill-editor-wrapper {
    flex: 1;
    min-width: 0;
    position: relative;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
  }
  .quill-editor-wrapper .ql-toolbar {
    position: sticky;
    top: 0;
    z-index: 10;
    background: #fff;
  }
  .quill-toc-container {
    width: 200px;
    flex-shrink: 0;
    position: sticky;
    top: 0;
    overflow-y: auto;
    background: #fafafa;
    border-radius: 8px;
    padding: 16px;
    border: 1px solid #f0f0f0;
  }
  .quill-toc-title {
    font-weight: 600;
    font-size: 14px;
    color: #333;
    position: sticky;
    top: -16px;
    background: #fafafa;
    z-index: 1;
    margin: -16px -16px 0;
    padding: 16px 16px 12px;
    border-bottom: 1px solid #f0f0f0;
  }
  /* 目录项使用 <button>（可键盘 Tab 导航 + Enter 触发跳转），此处重置浏览器默认按钮样式 */
  .quill-toc-item {
    display: block;
    width: 100%;
    padding: 0;
    border: none;
    background: none;
    font: inherit;
    text-align: left;
    cursor: pointer;
    font-size: 13px;
    line-height: 22px;
    color: #666;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    transition: color 0.2s;
  }
  .quill-toc-item:hover {
    color: #1677ff;
  }
  .quill-toc-item:focus-visible {
    outline: 2px solid #1677ff;
    outline-offset: 2px;
    border-radius: 2px;
    color: #1677ff;
  }
  /* 编辑器正文排版：覆盖 Quill snow 主题对块级元素的 margin:0 重置，
     选择器加 .quill-editor-wrapper 前缀提高特异性确保生效，
     使编辑态与最终渲染态的阅读体验一致 */
  .quill-editor-wrapper .ql-editor {
    line-height: 1.8;
    font-size: 15px;
    color: #334155;
  }
  .quill-editor-wrapper .ql-editor p,
  .quill-editor-wrapper .ql-editor ol,
  .quill-editor-wrapper .ql-editor ul,
  .quill-editor-wrapper .ql-editor pre,
  .quill-editor-wrapper .ql-editor blockquote,
  .quill-editor-wrapper .ql-editor .ql-callout {
    margin: 0 0 0.9em;
    padding: 0;
  }
  /* 提示框的 padding 需单独声明：上面的统一重置把它也清零了 */
  .quill-editor-wrapper .ql-editor .ql-callout {
    padding: 0.75em 1em;
  }
  .quill-editor-wrapper .ql-editor p:last-child,
  .quill-editor-wrapper .ql-editor ol:last-child,
  .quill-editor-wrapper .ql-editor ul:last-child,
  .quill-editor-wrapper .ql-editor pre:last-child,
  .quill-editor-wrapper .ql-editor blockquote:last-child,
  .quill-editor-wrapper .ql-editor .ql-callout:last-child {
    margin-bottom: 0;
  }
  .quill-editor-wrapper .ql-editor h1,
  .quill-editor-wrapper .ql-editor h2,
  .quill-editor-wrapper .ql-editor h3,
  .quill-editor-wrapper .ql-editor h4,
  .quill-editor-wrapper .ql-editor h5,
  .quill-editor-wrapper .ql-editor h6 {
    margin: 1.3em 0 0.6em;
    padding: 0;
    font-weight: 600;
    line-height: 1.4;
  }
  .quill-editor-wrapper .ql-editor h1 { font-size: 1.7em; }
  .quill-editor-wrapper .ql-editor h2 { font-size: 1.45em; }
  .quill-editor-wrapper .ql-editor h3 { font-size: 1.25em; }
  .quill-editor-wrapper .ql-editor h4 { font-size: 1.1em; }
  .quill-editor-wrapper .ql-editor h5 { font-size: 1em; }
  .quill-editor-wrapper .ql-editor h6 { font-size: 0.9em; color: #64748b; }
  .quill-editor-wrapper .ql-editor ol,
  .quill-editor-wrapper .ql-editor ul {
    padding-left: 1.5em;
  }
  .quill-editor-wrapper .ql-editor li {
    margin-bottom: 0.3em;
  }
  .quill-editor-wrapper .ql-editor blockquote {
    border-left: 4px solid #d1d5db;
    padding-left: 1em;
    color: #6b7280;
  }
  .quill-editor-wrapper .ql-editor pre {
    background: #1e293b;
    color: #f8fafc;
    padding: 0.9em 1em;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 0.875em;
    line-height: 1.6;
  }
  .quill-editor-wrapper .ql-editor pre.ql-syntax {
    margin: 0 0 0.9em;
  }
  .quill-editor-wrapper .ql-editor code {
    background: #f1f5f9;
    padding: 0.15em 0.4em;
    border-radius: 4px;
    font-size: 0.875em;
  }
  .quill-editor-wrapper .ql-editor pre code {
    background: transparent;
    padding: 0;
    color: inherit;
  }
  .quill-editor-wrapper .ql-editor img {
    max-width: 100%;
    height: auto;
    border-radius: 8px;
    margin: 0.4em 0;
  }
  .quill-editor-wrapper .ql-editor a {
    color: #1677ff;
    text-decoration: underline;
  }
  .quill-editor-wrapper .ql-editor hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 1.5em 0;
  }
  .quill-editor-wrapper .ql-editor table {
    width: 100%;
    border-collapse: collapse;
    margin: 0 0 0.9em;
  }
  .quill-editor-wrapper .ql-editor th,
  .quill-editor-wrapper .ql-editor td {
    border: 1px solid #e2e8f0;
    padding: 0.5em 0.75em;
    text-align: left;
  }
  .quill-editor-wrapper .ql-editor th {
    background: #f8fafc;
    font-weight: 600;
  }
  /* 图片调整大小覆盖层：点击图片后显示虚线选框 + 右下角拖拽手柄。
     overlay 自身 pointer-events:none，不拦截对图片 / 正文的点击，仅手柄可交互；
     定位基于 wrapper（position:relative），滚动 / 内容变化时由 JS 重新同步 */
  .quill-img-resize-overlay {
    position: absolute;
    z-index: 20;
    pointer-events: none;
    border: 1.5px dashed #1677ff;
    border-radius: 2px;
    box-sizing: border-box;
  }
  /* 手动尺寸面板：选框上方悬浮（bottom:100% 使其向上生长，内容换行时不会被裁掉），
     含宽高等比联动、对齐、替换、Alt 文本与确定/重置 */
  .quill-img-resize-panel {
    position: absolute;
    bottom: calc(100% + 6px);
    left: -1.5px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 5px;
    max-width: min(620px, 88vw);
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 4px 8px;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
    pointer-events: auto;
    box-sizing: border-box;
  }
  .quill-img-size-input {
    width: 54px;
    height: 24px;
    border: 1px solid #d9d9d9;
    border-radius: 4px;
    text-align: center;
    font-size: 12px;
    line-height: 22px;
    color: #333;
    outline: none;
    padding: 0 2px;
    box-sizing: border-box;
  }
  .quill-img-size-input:focus {
    border-color: #1677ff;
  }
  .quill-img-alt-input {
    width: 110px;
    text-align: left;
  }
  .quill-img-resize-times,
  .quill-img-panel-sep {
    color: #cbd5e1;
    font-size: 12px;
  }
  .quill-img-resize-btn {
    border: none;
    background: none;
    cursor: pointer;
    font-size: 12px;
    color: #1677ff;
    padding: 2px 3px;
    line-height: 18px;
    border-radius: 4px;
  }
  .quill-img-resize-btn:hover {
    color: #4096ff;
    background: #f0f7ff;
  }
  .quill-img-resize-btn.ql-active {
    background: #e6f4ff;
    color: #0958d9;
    font-weight: 600;
  }
  .quill-img-resize-handle {
    position: absolute;
    right: -7px;
    bottom: -7px;
    width: 14px;
    height: 14px;
    background: #1677ff;
    border: 2px solid #fff;
    border-radius: 50%;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
    cursor: nwse-resize;
    pointer-events: auto;
  }
  /* 全屏模式：根布局脱离文档流覆盖视口，编辑器与目录铺满屏幕 */
  .quill-root-layout.quill-fullscreen {
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: #fff;
    padding: 16px;
    box-sizing: border-box;
  }
  .quill-root-layout.quill-fullscreen .quill-editor-wrapper {
    height: auto;
    border: none;
    border-radius: 0;
  }
  .quill-root-layout.quill-fullscreen .quill-toc-container {
    height: auto;
    max-height: none;
  }
`
