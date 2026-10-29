import React from 'react'
import ReactDOM from 'react-dom/client'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import { themeConfig } from './theme'
import { bindToastInstance } from './utils/toast'
import { initMonitoring } from './utils/monitoring'
import 'antd/dist/reset.css'
import './styles/custom.css'

// 错误监控初始化：未配置 VITE_SENTRY_DSN 时空转、不下载 SDK；与用户端共用同一 DSN（按 URL 区分）
void initMonitoring()

/**
 * 把 antd `App` 提供的 message 实例接给 `utils/toast`（MNT-07）。
 *
 * 静态 `message.xxx()` 不消费 ConfigProvider 的 context：主题 / locale 对它无效，
 * 且每次调用都新挂一个 root（与 Modal 的层级关系不可控、组件卸载后残留）。
 * 这里在根上绑定一次，业务代码统一走 `toast`，拿到的就是带 context 的实例。
 */
function ToastBridge() {
  const { message } = AntdApp.useApp()
  React.useEffect(() => {
    bindToastInstance(message)
  }, [message])
  return null
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* 主题 token：次要文字压深到 WCAG AA（详见 src/theme.ts）；
        顺带显式 zh_CN —— 此前没设 locale，antd 内置文案（分页、空态、Modal
        按钮）走的是英文默认值。 */}
    <AntdApp>
      <ConfigProvider locale={zhCN} theme={themeConfig}>
        <ToastBridge />
        <App />
      </ConfigProvider>
    </AntdApp>
  </React.StrictMode>,
)
