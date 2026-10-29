/**
 * 富文本编辑器样式表：STATIC_EDITOR_CSS 为基础静态样式，EDITOR_EXTRA_CSS 为
 * 表格/图片对齐/代码块等扩展样式。内容块 class 与 quillBlots.ts 及前台 custom.css 同名对齐。
 */

// 具体规则拆到 editorStyles/{extra,static}.ts（MNT-08）；这里只做再导出，
// 外部 import 路径保持不变。
export { EDITOR_EXTRA_CSS } from './editorStyles/extra'
export { STATIC_EDITOR_CSS } from './editorStyles/static'
