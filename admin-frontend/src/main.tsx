import React from 'react'
import ReactDOM from 'react-dom/client'
import { App as AntdApp, ConfigProvider } from 'antd'
import enUS from 'antd/locale/en_US'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import App from './App'
import { getThemeConfig } from './theme'
import { resolveMode, usePrefsStore } from './store/prefsStore'
import { bindToastInstance } from './utils/toast'
import { initMonitoring } from './utils/monitoring'
import 'antd/dist/reset.css'
import './styles/custom.css'

// 错误监控初始化：未配置 VITE_SENTRY_DSN 时空转、不下载 SDK；与用户端共用同一 DSN（按 URL 区分）
void initMonitoring()

/**
 * 首帧同步主题标记：tokens.css 的 `[data-theme='dark']` 必须在**第一次绘制前**就绪。
 *
 * antd 的 ConfigProvider.theme 是运行时注入，首帧就能生效；但 `adm-*` 手写样式靠
 * CSS 变量，若等 ThemeShell 的 useEffect 才写 data-theme，暗色用户刷新会先闪一帧
 * 亮色卡片，再被切成暗色。persist 同步读 localStorage，这里拿到的就是上次的选择；
 * `system` 模式也在这里解析一次，随后由 ThemeShell 的 matchMedia 监听接管实时变化。
 */
function applyInitialTheme(): void {
  const resolved = resolveMode(usePrefsStore.getState().mode)
  document.documentElement.dataset.theme = resolved
  document.documentElement.style.colorScheme = resolved
}
applyInitialTheme()

/** 首帧语言：`<html lang>` 与 dayjs（日期选择器/日历的月份、星期名）在渲染前就位 */
function applyLocale(locale: string): void {
  const lang = locale === 'en-US' ? 'en' : 'zh-cn'
  document.documentElement.lang = locale
  dayjs.locale(lang)
}
applyLocale(usePrefsStore.getState().locale)

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

/**
 * 主题外壳：把「用户选的主题模式」解析成实际亮/暗，同时喂给两套体系 ——
 *
 * 1. antd 的 `ConfigProvider.theme`（组件内部颜色）；
 * 2. `document.documentElement[data-theme]`（`styles/sections/tokens.css` 里的 CSS 变量，
 *    供 `adm-*` 手写样式与布局内联样式使用）。
 *
 * 两套必须同时切：只切 antd 会出现「按钮变暗了、手写卡片还是白的」这种半截暗色。
 * `system` 模式要监听 `matchMedia`，否则用户改系统主题后页面不会跟。
 */
function ThemeShell({ children }: { children: React.ReactNode }) {
  const mode = usePrefsStore((s) => s.mode)
  const locale = usePrefsStore((s) => s.locale)
  const [systemDark, setSystemDark] = React.useState(
    () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches,
  )

  React.useEffect(() => {
    if (mode !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    setSystemDark(mq.matches)
    return () => mq.removeEventListener('change', onChange)
  }, [mode])

  const resolved = mode === 'system' ? (systemDark ? 'dark' : 'light') : resolveMode(mode)

  React.useEffect(() => {
    document.documentElement.dataset.theme = resolved
    // 原生控件（滚动条、日期选择器弹层）跟随，避免暗色页面里滚出一条亮白色滚动条
    document.documentElement.style.colorScheme = resolved
  }, [resolved])

  React.useEffect(() => {
    applyLocale(locale)
  }, [locale])

  return (
    <ConfigProvider locale={locale === 'en-US' ? enUS : zhCN} theme={getThemeConfig(resolved)}>
      {children}
    </ConfigProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* 主题 token：次要文字压深到 WCAG AA（详见 src/theme.ts）。
        ConfigProvider 包在 AntdApp 外层：message/Modal 这些 App 提供的实例
        才能拿到当前主题与语言（反过来的话它们退回 antd 默认值）。 */}
    <ThemeShell>
      <AntdApp>
        <ToastBridge />
        <App />
      </AntdApp>
    </ThemeShell>
  </React.StrictMode>,
)
