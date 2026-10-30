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

export function closeAllTabs(): string[] {
  return [HOME_TAB]
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
