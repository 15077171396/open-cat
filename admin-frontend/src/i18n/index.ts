import { useCallback } from 'react'
import { usePrefsStore } from '../store/prefsStore'
import { SUPPORTED_LOCALES, hasMessage, isLocale, translate } from './messages'
import type { Locale } from './messages'

export type { Locale }
export { SUPPORTED_LOCALES, hasMessage, isLocale, translate }

/** 语言名称按惯例用各自的语言书写，不参与翻译 */
export const LOCALE_LABELS: Record<Locale, string> = {
  'zh-CN': '简体中文',
  'en-US': 'English',
}

/**
 * 组件内取当前语言与翻译函数。
 *
 * 语言存在 `prefsStore`（与主题/布局同一份持久化）：它是用户显式选择的展示偏好，
 * 退出登录不该丢。`t` 在语言切换时换引用，放进 useMemo/useEffect 依赖即可重算。
 */
export function useI18n() {
  const locale = usePrefsStore((s) => s.locale)
  const setLocale = usePrefsStore((s) => s.setLocale)

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => translate(locale, key, vars),
    [locale],
  )

  return { locale, setLocale, t }
}

/** 非组件上下文（axios 拦截器等）读最新语言，避免把 locale 变成模块级快照 */
export function currentLocale(): Locale {
  return usePrefsStore.getState().locale
}

export function translateNow(key: string, vars?: Record<string, string | number>): string {
  return translate(currentLocale(), key, vars)
}
