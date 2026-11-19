/**
 * 教程分类（**兜底值**，实际选项由 `useCategoryOptions('tutorial', ...)` 从「分类管理」拉取）。
 * 只在分类管理接口不可用/还没建分类时兜底；保持中性，不预设任何业务专属分类。
 * ⚠️ 首项同时是新建教程的默认分类（`Tutorials/Edit.tsx` 用 `TUTORIAL_CATEGORIES[0]`）。
 */
export const TUTORIAL_CATEGORIES = ['入门', '运营', '进阶'] as const

/** 教程更新状态 */
export const TUTORIAL_STATUSES = [
  { value: 'updating', label: '更新中' },
  { value: 'completed', label: '已完结' },
] as const

/** 默认分页大小 */
export const DEFAULT_PAGE_SIZE = 20

/**
 * 管理员令牌存储键名。刻意用 sessionStorage 而非 localStorage：与前台同源，
 * 前台渲染的不可信内容任一处 XSS 都能读到长效 refresh token，sessionStorage
 * 至少保证关标签页即失效。局限：不能防 XSS，只是缩小暴露窗口。
 */
export const AUTH_TOKEN_KEY = 'ai_cat_admin_token'
export const AUTH_REFRESH_KEY = 'ai_cat_admin_refresh'
