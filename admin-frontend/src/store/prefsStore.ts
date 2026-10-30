import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { isLocale } from '../i18n/messages'
import type { Locale } from '../i18n/messages'

/** 主题模式：跟随系统时由 `matchMedia` 实时决定 */
export type ThemeMode = 'light' | 'dark' | 'system'
/** 布局：侧边栏导航 / 顶栏导航 */
export type LayoutMode = 'side' | 'top'

interface PrefsState {
  mode: ThemeMode
  layout: LayoutMode
  /** 界面语言：与主题/布局同属展示偏好，退出登录不丢 */
  locale: Locale
  setMode: (mode: ThemeMode) => void
  setLayout: (layout: LayoutMode) => void
  setLocale: (locale: Locale) => void
}

/**
 * 界面偏好（主题 / 布局 / 语言）。
 *
 * 为什么单独一个 store 而不是塞进 authStore：这几项是**纯展示偏好**，登录态被清空
 * （退出、401）时不该跟着丢 —— 用户下次登录还希望是暗色、英文。
 *
 * 持久化到 localStorage（`persist`）：刷新后保持；键名前缀 `adm-` 与其它本地存储区分。
 * 只存用户显式选过的值，不存「当前解析出来的亮/暗」—— 否则改了系统设置就不跟手。
 */
export const usePrefsStore = create<PrefsState>()(
  persist(
    (set) => ({
      mode: 'light',
      layout: 'side',
      locale: 'zh-CN',
      setMode: (mode) => set({ mode }),
      setLayout: (layout) => set({ layout }),
      setLocale: (locale) => set({ locale }),
    }),
    {
      name: 'adm-ui-prefs',
      version: 1,
      // 老版本持久化里没有 locale（或被人为改坏）：回落到默认中文，不能让 translate 拿到非法键
      merge: (persisted, current) => {
        const saved = (persisted ?? {}) as Partial<PrefsState>
        return {
          ...current,
          ...saved,
          locale: isLocale(saved.locale) ? saved.locale : current.locale,
        }
      },
    },
  ),
)

/** 把 `system` 解析成实际的亮/暗（非浏览器环境回退亮色，便于测试） */
export function resolveMode(mode: ThemeMode): 'light' | 'dark' {
  if (mode !== 'system') return mode
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}
