import { create } from 'zustand'

interface PageTitleState {
  titles: Record<string, string>
  setTitle: (pathname: string, title: string) => void
  clearTitle: (pathname: string) => void
  reset: () => void
}

/** 页面标题注册表：PageHeader 写入，PageTabs 读取。不持久化，随页面会话生效。 */
export const usePageTitleStore = create<PageTitleState>((set) => ({
  titles: {},
  setTitle: (pathname, title) =>
    set((state) => {
      if (!pathname || !title || state.titles[pathname] === title) return state
      return { titles: { ...state.titles, [pathname]: title } }
    }),
  clearTitle: (pathname) =>
    set((state) => {
      if (!(pathname in state.titles)) return state
      const next = { ...state.titles }
      delete next[pathname]
      return { titles: next }
    }),
  reset: () => set({ titles: {} }),
}))
