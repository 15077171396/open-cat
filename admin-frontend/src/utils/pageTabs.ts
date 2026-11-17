export const HOME_TAB = '/'
export const MAX_TABS = 10
const STORAGE_KEY = 'ai_cat_admin_tabs'
const STORAGE_VERSION = 1

interface TabSnapshot {
  v?: number
  tabs?: unknown
}

export function normalizeTabs(tabs: string[]): string[] {
  const unique: string[] = []
  for (const key of tabs) {
    if (typeof key !== 'string' || !key.startsWith('/')) continue
    if (!unique.includes(key)) unique.push(key)
  }
  if (!unique.includes(HOME_TAB)) unique.unshift(HOME_TAB)
  return unique
}

/** 超上限时保留首页、当前页与最新的标签，淘汰最旧的非当前页 */
export function limitTabs(tabs: string[], activeKey: string): string[] {
  const normalized = normalizeTabs(tabs)
  if (normalized.length <= MAX_TABS) return normalized
  const rest = normalized.filter((key) => key !== HOME_TAB)
  const tail = rest.slice(-(MAX_TABS - 1))
  if (activeKey !== HOME_TAB && !tail.includes(activeKey)) {
    tail.shift()
    tail.push(activeKey)
  }
  return [HOME_TAB, ...tail]
}

export function addTab(tabs: string[], key: string): string[] {
  const normalized = normalizeTabs(tabs)
  if (normalized.includes(key)) return normalized
  return limitTabs([...normalized, key], key)
}

export function removeTab(tabs: string[], key: string): string[] {
  if (key === HOME_TAB) return normalizeTabs(tabs)
  return normalizeTabs(tabs.filter((item) => item !== key))
}

/** 关闭当前标签后应该跳转的路径；关闭非当前标签返回 null（不跳转） */
export function nextTabAfterClose(
  tabs: string[],
  key: string,
  activeKey: string,
): string | null {
  if (key !== activeKey) return null
  const remaining = removeTab(tabs, key)
  const index = tabs.indexOf(key)
  const left = remaining[Math.min(Math.max(index - 1, 0), remaining.length - 1)]
  return left ?? HOME_TAB
}

export function closeOtherTabs(tabs: string[], key: string): string[] {
  return normalizeTabs(tabs.filter((item) => item === HOME_TAB || item === key))
}

/** 关闭左侧：保留首页、当前页、当前页右侧以及固定标签（演示站「关闭左侧」） */
export function closeLeftTabs(tabs: string[], key: string, pinned: string[] = []): string[] {
  const index = tabs.indexOf(key)
  if (index <= 0) return normalizeTabs(tabs)
  return normalizeTabs(
    tabs.filter(
      (item, itemIndex) => itemIndex >= index || item === HOME_TAB || pinned.includes(item),
    ),
  )
}

/** 关闭右侧：保留首页、当前页、当前页左侧以及固定标签（演示站「关闭右侧」） */
export function closeRightTabs(tabs: string[], key: string, pinned: string[] = []): string[] {
  const index = tabs.indexOf(key)
  if (index < 0) return normalizeTabs(tabs)
  return normalizeTabs(
    tabs.filter(
      (item, itemIndex) => itemIndex <= index || item === HOME_TAB || pinned.includes(item),
    ),
  )
}

export function closeAllTabs(): string[] {
  return [HOME_TAB]
}

/** 固定标签：演示站「固定」后关闭其他/全部不误删，持久化在 localStorage */
const PIN_KEY = 'adm-tab-pins'

export function readPinnedTabs(storage: Pick<Storage, 'getItem'>): string[] {
  try {
    const raw = storage.getItem(PIN_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as { v?: number; pins?: unknown }
    if (parsed?.v !== STORAGE_VERSION || !Array.isArray(parsed.pins)) return []
    return parsed.pins.filter(
      (key): key is string => typeof key === 'string' && key.startsWith('/'),
    )
  } catch {
    return []
  }
}

export function writePinnedTabs(storage: Pick<Storage, 'setItem'>, pins: string[]): void {
  try {
    storage.setItem(PIN_KEY, JSON.stringify({ v: STORAGE_VERSION, pins }))
  } catch {
    // 隐私模式 / 配额满：固定标签只是便利功能，写不进去不影响主流程
  }
}

export function readTabs(storage: Pick<Storage, 'getItem'>): string[] {
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return [HOME_TAB]
    const parsed = JSON.parse(raw) as TabSnapshot
    if (parsed?.v !== STORAGE_VERSION || !Array.isArray(parsed.tabs)) return [HOME_TAB]
    return normalizeTabs(parsed.tabs.filter((key): key is string => typeof key === 'string'))
  } catch {
    return [HOME_TAB]
  }
}

export function writeTabs(storage: Pick<Storage, 'setItem'>, tabs: string[]): void {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify({ v: STORAGE_VERSION, tabs: normalizeTabs(tabs) }))
  } catch {
    // 隐私模式 / 配额满：标签页只是便利功能，写不进去不影响主流程
  }
}
