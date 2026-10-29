/**
 * 全局提示（message）的统一入口（第五轮审查 MNT-07）。
 *
 * ## 为什么不能直接用 antd 的静态 message
 *
 * antd 的静态方法（`import { message } from 'antd'`）**不消费 context**：
 * 每次调用都会自己挂一个独立的 root 节点，于是
 *
 * - 主题（ConfigProvider 的 token / 暗色模式）与 locale 对它无效 —— 后台切主题后
 *   提示框还是默认样式，文案也不跟随 locale；
 * - 每个调用点各自持有一份容器，同一时刻多条提示会分散在不同层级，
 *   层级关系（与 Modal/Drawer 的 z-index）不可控；
 * - 组件卸载后残留的 root 不会自动回收，长会话里会累积。
 *
 * 正确做法是 `App.useApp()` 拿到的实例（它消费 context）。但项目里有 150+ 处
 * 调用点在**非组件**位置（api 回调、hooks、工具函数），拿不到 hook。
 *
 * ## 做法：桥接
 *
 * 根组件里用 `bindToastInstance(AntdApp.useApp().message)` 绑定一次，
 * 业务代码统一 `import { toast } from '.../utils/toast'` 调用。
 * 绑定前（或绑定失败）自动回退到静态 message —— 提示不会丢，只是失去主题。
 */

import { message as staticMessage } from 'antd'
import type { MessageInstance } from 'antd/es/message/interface'

let bound: MessageInstance | null = null

/** 由根组件调用一次，把带 context 的 message 实例接进来 */
export function bindToastInstance(instance: MessageInstance): void {
  bound = instance
}

function get(): MessageInstance {
  return bound ?? staticMessage
}

/**
 * 与 antd message 同签名的薄封装。
 *
 * 用 `Parameters<typeof staticMessage.xxx>` 而不是自己写类型：antd 的
 * `JointContent` / `ArgsProps` 会随版本变，抄一份就会在升级时悄悄失配。
 */
export const toast = {
  success: (...args: Parameters<typeof staticMessage.success>) => get().success(...args),
  error: (...args: Parameters<typeof staticMessage.error>) => get().error(...args),
  warning: (...args: Parameters<typeof staticMessage.warning>) => get().warning(...args),
  info: (...args: Parameters<typeof staticMessage.info>) => get().info(...args),
  loading: (...args: Parameters<typeof staticMessage.loading>) => get().loading(...args),
  open: (...args: Parameters<typeof staticMessage.open>) => get().open(...args),
  destroy: (...args: Parameters<typeof staticMessage.destroy>) => get().destroy(...args),
}
