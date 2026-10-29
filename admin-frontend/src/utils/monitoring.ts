// 前端错误监控（Sentry/GlitchTip）门控初始化。
// ⚠️ 与 admin-frontend/src/utils/monitoring.ts 保持逐字一致（两个前端独立工程的复制惯例），改一侧必须同步另一侧。
// 门控按需加载：仅配置 VITE_SENTRY_DSN 才动态 import SDK（独立 chunk 不占主包）；
// 隐私：登录注册的明文口令在「源头」不采集，beforeSend/beforeBreadcrumb 再擦一遍作纵深防御；
// scrub* 系列均为纯函数，可离线跑 Node 回归。
import type { Breadcrumb, BrowserOptions, ErrorEvent, EventHint } from '@sentry/react'

const SENSITIVE_KEY_PARTS = [
  'password',
  'passwd',
  'pwd',
  'token',
  'secret',
  'authorization',
  'cookie',
  'session',
  'api_key',
  'apikey',
  'access_key',
  'private_key',
  'code',
  'credential',
]

const REDACTED = '***'

/**
 * 自由文本里的凭据，匹配 `key=value` / `key: value` / `"key": "value"` 三种写法。
 * 键名允许带前缀（refresh_token）故用 `[A-Za-z0-9_]*`（\b 会让 refresh_token 漏掉）；
 * 不用后行断言（Safari 16.4 以下不支持，会抛语法错误）。
 */
const CREDENTIAL_RE =
  /([A-Za-z0-9_]*(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|private[_-]?key|credential|authorization|code)|["'](?:password|passwd|pwd|token|secret|api[_-]?key|code)["'])(\s*[=:]\s*)(["']?)([^\s,;&"'}]+)/gi

function isSensitiveKey(key: string): boolean {
  const lowered = key.toLowerCase()
  return SENSITIVE_KEY_PARTS.some((part) => lowered.includes(part))
}

/** 擦除自由文本中的凭据。可能误伤 `status code: 200`，属刻意取舍（宁可多擦）。 */
export function scrubText(text: string): string {
  return text.replace(CREDENTIAL_RE, (_m, key, sep, quote) => `${key}${sep}${quote}${REDACTED}`)
}

/** 递归擦除对象中的敏感字段值 */
export function scrubValue(value: unknown, depth = 0): unknown {
  if (depth >= 6) return value
  if (Array.isArray(value)) return value.map((item) => scrubValue(item, depth + 1))
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = isSensitiveKey(k) ? REDACTED : scrubValue(v, depth + 1)
    }
    return out
  }
  if (typeof value === 'string') return scrubText(value)
  return value
}

/** 浏览器扩展注入的报错、以及无排查价值的常见噪声。 */
const IGNORE_ERRORS = [
  'ResizeObserver loop limit exceeded',
  'ResizeObserver loop completed with undelivered notifications',
  'Non-Error promise rejection captured',
  'Loading chunk',
  'Loading CSS chunk',
  'ChunkLoadError',
  'Failed to fetch dynamically imported module',
]

const IGNORE_URLS = [/^chrome-extension:\/\//, /^moz-extension:\/\//, /^safari-extension:\/\//]

/** 上报前钩子：擦除请求与消息中的凭据。 */
export function scrubEvent(event: ErrorEvent, _hint?: EventHint): ErrorEvent {
  // 请求体/查询串/cookie 一律不带出：登录注册接口的明文口令就在这里
  if (event.request) {
    delete event.request.data
    delete event.request.query_string
    delete event.request.cookies
    if (event.request.headers) {
      const headers: Record<string, string> = {}
      for (const [k, v] of Object.entries(event.request.headers)) {
        headers[k] = isSensitiveKey(k) ? REDACTED : v
      }
      event.request.headers = headers
    }
  }
  if (event.extra) event.extra = scrubValue(event.extra) as Record<string, unknown>
  if (typeof event.message === 'string') event.message = scrubText(event.message)
  event.exception?.values?.forEach((item) => {
    if (typeof item.value === 'string') item.value = scrubText(item.value)
  })
  event.breadcrumbs?.forEach((item) => {
    if (typeof item.message === 'string') item.message = scrubText(item.message)
  })
  // 用户信息只保留 ID，去掉邮箱等可直接定位到自然人的字段
  if (event.user) event.user = event.user.id ? { id: event.user.id } : {}
  return event
}

/** 面包屑钩子：丢弃表单输入类面包屑，其余擦除凭据。 */
export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb | null {
  // ui.input 面包屑会记录用户输入的表单值（登录页即明文口令）
  if (breadcrumb.category === 'ui.input') return null
  if (typeof breadcrumb.message === 'string') {
    breadcrumb.message = scrubText(breadcrumb.message)
  }
  return breadcrumb
}

export interface MonitoringConfig {
  dsn: string
  environment: string
  release: string
  tracesSampleRate: number
}

/** 构造 Sentry 初始化参数。纯函数：不读环境变量、不加载 SDK，便于离线回归。 */
export function buildSentryOptions(config: MonitoringConfig): BrowserOptions {
  return {
    dsn: config.dsn,
    environment: config.environment,
    release: config.release || undefined,
    // 采集范围收口：在「源头」不采集，beforeSend 作纵深防御；登录/注册的明文口令就在请求体与表单输入里。
    // 注：v11 已用 dataCollection 取代 sendDefaultPii，不再传该选项
    dataCollection: {
      userInfo: false, // 不回填 user.email / ip_address
      cookies: false,
      httpHeaders: { request: { deny: ['authorization', 'cookie', 'x-api-key'] } },
      httpBodies: [], // 请求体一律不采集（登录/注册的明文口令在此）
      urlQueryParams: false,
      graphQL: { document: false, variables: false },
      genAI: { inputs: false, outputs: false },
      // 前端产物经压缩混淆，变量名已不可读（password 会变成 a），
      // 按名脱敏必然失效，因此直接不采集局部变量
      stackFrameVariables: false,
      frameContextLines: 0, // 不附带源码行
    },
    // 性能追踪默认关闭（0）；需要时用环境变量开启，避免自带采样开销
    tracesSampleRate: config.tracesSampleRate,
    ignoreErrors: IGNORE_ERRORS,
    denyUrls: IGNORE_URLS,
    beforeSend: scrubEvent,
    beforeBreadcrumb: scrubBreadcrumb,
  }
}

let loading: Promise<boolean> | null = null
let enabled = false

function readEnv(key: string): string {
  // `?? {}` 兜底：脱离 Vite 运行（如 Node 类型剥离回归）时 import.meta.env 为 undefined
  const env = (import.meta.env ?? {}) as Record<string, string | undefined>
  return (env[key] ?? '').trim()
}

/** 初始化前端错误监控，返回是否真正启用。幂等；未配置 DSN 时同步返回 false，调用方无需 await */
export function initMonitoring(): Promise<boolean> {
  if (loading) return loading

  const dsn = readEnv('VITE_SENTRY_DSN')
  if (!dsn) {
    loading = Promise.resolve(false)
    return loading
  }

  const config: MonitoringConfig = {
    dsn,
    environment: readEnv('VITE_SENTRY_ENVIRONMENT') || import.meta.env.MODE,
    release: readEnv('VITE_APP_RELEASE'),
    tracesSampleRate: Number(readEnv('VITE_SENTRY_TRACES_SAMPLE_RATE')) || 0,
  }

  loading = import('@sentry/react')
    .then((Sentry) => {
      Sentry.init(buildSentryOptions(config))
      enabled = true
      return true
    })
    .catch((err) => {
      // 监控自身故障不得影响页面：SDK 加载失败只记一条警告
      console.warn('[monitoring] 前端错误监控初始化失败，已跳过:', err)
      return false
    })

  return loading
}

export function isMonitoringEnabled(): boolean {
  return enabled
}
