// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { usePrefsStore, resolveMode } from './prefsStore'

const PREFS_KEY = 'adm-ui-prefs'

/** jsdom 不实现 matchMedia，按用例需要注入一个可控实现 */
function mockMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((media: string) => ({
      matches,
      media,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

describe('prefsStore（主题 / 布局展示偏好）', () => {
  beforeEach(() => {
    localStorage.clear()
    // store 是模块级单例：每个用例重置到默认值，避免相互污染
    usePrefsStore.setState({ mode: 'light', layout: 'side', locale: 'zh-CN' })
  })

  it('默认亮色 + 侧边导航 + 中文', () => {
    expect(usePrefsStore.getState().mode).toBe('light')
    expect(usePrefsStore.getState().layout).toBe('side')
    expect(usePrefsStore.getState().locale).toBe('zh-CN')
  })

  it('setMode / setLayout / setLocale 更新状态并持久化到 localStorage', () => {
    usePrefsStore.getState().setMode('dark')
    usePrefsStore.getState().setLayout('top')
    usePrefsStore.getState().setLocale('en-US')
    expect(usePrefsStore.getState().mode).toBe('dark')
    expect(usePrefsStore.getState().layout).toBe('top')
    expect(usePrefsStore.getState().locale).toBe('en-US')

    const raw = localStorage.getItem(PREFS_KEY)
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw!).state).toMatchObject({ mode: 'dark', layout: 'top', locale: 'en-US' })
  })

  it('老持久化数据没有 locale 时回落到中文', async () => {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ state: { mode: 'dark', layout: 'side' }, version: 1 }),
    )
    await usePrefsStore.persist.rehydrate()
    expect(usePrefsStore.getState().mode).toBe('dark')
    expect(usePrefsStore.getState().locale).toBe('zh-CN')
  })

  it('持久化只存用户显式选择，不存解析出来的亮/暗', () => {
    usePrefsStore.getState().setMode('system')
    const state = JSON.parse(localStorage.getItem(PREFS_KEY)!).state
    expect(state.mode).toBe('system')
    // 存解析结果会导致改了系统设置后页面不跟手
    expect(state).not.toHaveProperty('resolved')
  })

  it('resolveMode：显式亮 / 暗直接返回', () => {
    expect(resolveMode('light')).toBe('light')
    expect(resolveMode('dark')).toBe('dark')
  })

  it('resolveMode：无 matchMedia 的环境里 system 回退亮色', () => {
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
    expect(resolveMode('system')).toBe('light')
  })

  it('resolveMode：system 跟随系统偏好实时变化', () => {
    mockMatchMedia(true)
    expect(resolveMode('system')).toBe('dark')
    mockMatchMedia(false)
    expect(resolveMode('system')).toBe('light')
  })
})
