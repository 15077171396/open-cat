/**
 * Quill 的两处模块级兼容性补丁（第二轮复审 M-01 的安全拆分）。
 *
 * 这两段代码与组件生命周期无关：只要 import 到就会执行一次，作用是修
 * `quill-table-better` 与 `react-quill-new` 的已知不兼容。放在独立模块里，
 * 是为了让「为什么必须 monkey-patch 第三方」这件事有一个可检索的落点，
 * 而不是埋在 1300 行的编辑器组件中间。
 *
 * ⚠️ 引入顺序：本模块在 `Quill.register({ 'modules/table-better': ... })` 之前执行
 * （ESM import 提升），两处补丁都只依赖 `Quill.prototype` / `parchment`，不依赖注册结果，
 * 因此先后无关。
 */

import { Quill } from 'react-quill-new'
import Delta from 'quill-delta'

/**
 * 兼容性修复 1：quill-table-better 的表格 blot 经 setContents 全量重建时无法正确构建
 * （插件 README 要求用 updateContents 初始化数据，而 react-quill-new 内部调用的正是
 * setContents），故把 setContents 重定向为语义等价的 updateContents 全量替换。
 *
 * ⚠️ updateContents 不保证文档以 \n 结尾，必须手动补齐并合并进最后一个 insert op，
 * 否则 applyDelta 的 newline 校正会吞掉刚插入的正文（切章自动保存会把整章清空）。
 */
Quill.prototype.setContents = function (this: Quill, delta, source) {
  const next = delta instanceof Delta ? delta : new Delta(delta)
  const ops = (next.ops || []).slice()
  const last = ops[ops.length - 1]
  if (last && typeof last.insert === 'string') {
    if (!last.insert.endsWith('\n')) {
      ops[ops.length - 1] = { ...last, insert: `${last.insert}\n` }
    }
  } else {
    // 空内容（或以 embed/非文本 op 结尾）：补换行，保证清空后文档回到 "\n"
    ops.push({ insert: '\n' })
  }
  const normalized = new Delta(ops)
  return this.updateContents(new Delta().delete(this.getLength()).concat(normalized), source)
}

/**
 * 兼容性修复 2：quill-table-better 的 setClassName 在表格缺 <temporary> 子节点时
 * （前台 DOMPurify 会剥离该内部标记）会无条件调用前一个兄弟 blot 的 descendant，
 * 前一个兄弟可能是叶 blot 而抛错。给叶 blot 基类补安全默认实现
 * （语义与 ContainerBlot 未命中一致），比 patch 插件私有方法更稳。
 */
{
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const LeafBlot: any = Quill.import('parchment').LeafBlot
  /* eslint-enable @typescript-eslint/no-explicit-any */
  if (LeafBlot && typeof LeafBlot.prototype.descendant !== 'function') {
    // 与 ContainerBlot.descendant(criteria, index) 未命中时的返回约定一致
    LeafBlot.prototype.descendant = () => [null, -1]
    LeafBlot.prototype.descendants = () => []
  }
}
