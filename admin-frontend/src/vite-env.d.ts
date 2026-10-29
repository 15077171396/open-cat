/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 前端错误监控 DSN（Sentry / 自建 GlitchTip）。留空 = 不启用，SDK 不下载。 */
  readonly VITE_SENTRY_DSN?: string
  /** 上报环境名，留空取 import.meta.env.MODE */
  readonly VITE_SENTRY_ENVIRONMENT?: string
  /** 发布版本号（建议填 git short sha） */
  readonly VITE_APP_RELEASE?: string
  /** 性能追踪采样率，默认 0（只上报错误） */
  readonly VITE_SENTRY_TRACES_SAMPLE_RATE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
