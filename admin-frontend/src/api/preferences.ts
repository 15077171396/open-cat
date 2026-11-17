import { apiGet, apiPost, apiPut } from './client'
import type { PrefsState } from '../store/prefsStore'

/** 服务端偏好（snake_case，与后端 Pydantic 白名单一一对应） */
export interface ServerPreferences {
  mode: string
  layout: string
  locale: string
  primary_color: string
  menu_style: string
  box_style: string
  page_width: string
  menu_width: number
  tab_style: string
  page_transition: string
  radius: number
  component_size: string
  show_tabs: boolean
  accordion: boolean
  show_menu_button: boolean
  show_fast_enter: boolean
  show_refresh_button: boolean
  show_crumbs: boolean
  show_language: boolean
  show_menu_search: boolean
  show_fullscreen: boolean
  show_size_select: boolean
  show_notification: boolean
  show_nprogress: boolean
  color_weak: boolean
  watermark: boolean
  show_app_logo: boolean
  show_guide: boolean
  gray_mode: boolean
  ai_assistant: boolean
  guide_done: boolean
}

export interface PreferencesData {
  exists: boolean
  preferences: ServerPreferences | null
  has_lock_password: boolean
}

export interface LockVerifyData {
  unlock_token: string
  expires_in: number
}

/** 读取本人偏好；无记录时 exists=false（调用方保留本地值） */
export function fetchPreferences(): Promise<PreferencesData> {
  return apiGet<PreferencesData>('/admin/preferences')
}

/** 增量保存本人偏好（后端按白名单过滤未知字段） */
export function savePreferences(payload: ServerPreferences): Promise<PreferencesData> {
  return apiPut<PreferencesData>('/admin/preferences', payload)
}

/** 设置/更新锁屏口令（服务端 bcrypt 存储） */
export function saveLockPassword(password: string): Promise<{ has_lock_password: boolean }> {
  return apiPost<{ has_lock_password: boolean }>('/admin/lock/password', { password })
}

/** 校验锁屏口令，返回 5 分钟解锁令牌 */
export function verifyLockPassword(password: string): Promise<LockVerifyData> {
  return apiPost<LockVerifyData>('/admin/lock/verify', { password })
}

/** 服务端字段 → 本地 store 字段（未知字段不会出现在结果里，由 store 再校验） */
export function fromServerPreferences(raw: ServerPreferences): Record<string, unknown> {
  return {
    mode: raw.mode,
    layout: raw.layout,
    locale: raw.locale,
    primaryColor: raw.primary_color,
    menuStyle: raw.menu_style,
    boxStyle: raw.box_style,
    pageWidth: raw.page_width,
    menuWidth: raw.menu_width,
    tabStyle: raw.tab_style,
    pageTransition: raw.page_transition,
    radius: raw.radius,
    componentSize: raw.component_size,
    showTabs: raw.show_tabs,
    accordion: raw.accordion,
    showMenuButton: raw.show_menu_button,
    showFastEnter: raw.show_fast_enter,
    showRefreshButton: raw.show_refresh_button,
    showCrumbs: raw.show_crumbs,
    showLanguage: raw.show_language,
    showMenuSearch: raw.show_menu_search,
    showFullscreen: raw.show_fullscreen,
    showSizeSelect: raw.show_size_select,
    showNotification: raw.show_notification,
    showNprogress: raw.show_nprogress,
    colorWeak: raw.color_weak,
    watermark: raw.watermark,
    showAppLogo: raw.show_app_logo,
    showGuide: raw.show_guide,
    grayMode: raw.gray_mode,
    aiAssistant: raw.ai_assistant,
    guideDone: raw.guide_done,
  }
}

/** 本地 store 字段 → 服务端字段（只取 31 个白名单字段） */
export function toServerPreferences(state: PrefsState): ServerPreferences {
  return {
    mode: state.mode,
    layout: state.layout,
    locale: state.locale,
    primary_color: state.primaryColor,
    menu_style: state.menuStyle,
    box_style: state.boxStyle,
    page_width: state.pageWidth,
    menu_width: state.menuWidth,
    tab_style: state.tabStyle,
    page_transition: state.pageTransition,
    radius: state.radius,
    component_size: state.componentSize,
    show_tabs: state.showTabs,
    accordion: state.accordion,
    show_menu_button: state.showMenuButton,
    show_fast_enter: state.showFastEnter,
    show_refresh_button: state.showRefreshButton,
    show_crumbs: state.showCrumbs,
    show_language: state.showLanguage,
    show_menu_search: state.showMenuSearch,
    show_fullscreen: state.showFullscreen,
    show_size_select: state.showSizeSelect,
    show_notification: state.showNotification,
    show_nprogress: state.showNprogress,
    color_weak: state.colorWeak,
    watermark: state.watermark,
    show_app_logo: state.showAppLogo,
    show_guide: state.showGuide,
    gray_mode: state.grayMode,
    ai_assistant: state.aiAssistant,
    guide_done: state.guideDone,
  }
}
