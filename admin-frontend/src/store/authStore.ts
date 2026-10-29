import { create } from 'zustand'
import client, { apiGet, apiPost } from '../api/client'
import { AUTH_TOKEN_KEY, AUTH_REFRESH_KEY } from '../constants'
import type { AdminUser } from '../types'

/** fetchUser 结果：判别联合，调用方按 kind 分支处理 */
export type FetchUserResult =
  | { kind: 'ok'; user: AdminUser }
  | { kind: 'unauthorized' } // 401：登录态已在 fetchUser 内清除
  | { kind: 'network' } // 网络错误：token 保留
  | { kind: 'http'; status: number; message: string } // 其他 HTTP 错误：token 保留

interface AuthState {
  user: AdminUser | null
  token: string | null
  isAuthenticated: boolean
  loading: boolean
  /** RBAC 权限码（动态菜单/按钮渲染依据；null=尚未加载） */
  permissions: string[] | null
  isSuper: boolean
  login: (account: string, password: string, totpCode?: string) => Promise<AdminUser>
  logout: () => Promise<void>
  fetchUser: () => Promise<FetchUserResult>
  /** 角色被变更后重新拉取 RBAC 档案 */
  refreshPermissions: () => Promise<void>
  /** 401 续期成功后写入新 token 对（后端会轮换 refresh_token） */
  onTokensRefreshed: (accessToken: string, refreshToken?: string) => void
  /** 清空认证状态并移除本地令牌 */
  clearAuth: () => void
}

// 模块级并发去重：并发调用共享同一次请求，避免慢响应覆盖快响应
let _fetchUserPromise: Promise<FetchUserResult> | null = null
let _refreshPermsPromise: Promise<void> | null = null

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  token: sessionStorage.getItem(AUTH_TOKEN_KEY),
  isAuthenticated: !!sessionStorage.getItem(AUTH_TOKEN_KEY),
  loading: false,
  permissions: null,
  isSuper: false,

  login: async (account: string, password: string, totpCode?: string) => {
    // totp_code 仅在启用二次验证时携带（后端缺码会回 401 + data.totp_required，前端据此补一步输入）
    const body: Record<string, string> = { account, password }
    if (totpCode) body.totp_code = totpCode
    const data = await apiPost<{ access_token: string; refresh_token: string }>('/auth/login', body)
    sessionStorage.setItem(AUTH_TOKEN_KEY, data.access_token)
    // refresh_token **刻意不落 storage**（SEC-12）：后端同时下发 httpOnly cookie，
    // 续期走 cookie（见 api/client.ts）。sessionStorage 对 XSS 完全可读，
    // 存一个 7 天有效、且能换出任意权限 access token 的凭据，
    // 等于把「一次 XSS」放大成「七天后台接管」。
    // 这里顺手清掉历史版本可能残留的值（老版本登录过的浏览器里还留着）。
    sessionStorage.removeItem(AUTH_REFRESH_KEY)
    set({ token: data.access_token, isAuthenticated: true })
    const result = await get().fetchUser()
    const user = result.kind === 'ok' ? result.user : null
    if (!user || user.role !== 'admin') {
      // 非 admin 或获取失败：回滚登录态
      get().clearAuth()
      throw new Error(user ? '该账号无管理后台权限' : '获取用户信息失败，请检查网络后重试')
    }
    return user
  },

  logout: async () => {
    const token = get().token
    // 不带 body：refresh_token 在 httpOnly cookie 里，后端会从 cookie 取并吊销
    // （access token 过期时也能走通，不会出现「401 被吞掉、服务端没吊销」）
    if (token || document.cookie.includes('refresh_token')) {
      try {
        await client.post('/auth/logout')
      } catch (err) {
        console.warn('logout 请求失败:', err)
      }
    }
    get().clearAuth()
  },

  onTokensRefreshed: (accessToken: string) => {
    // 只更新 access token：refresh token 由后端轮换并写回 httpOnly cookie
    sessionStorage.setItem(AUTH_TOKEN_KEY, accessToken)
    sessionStorage.removeItem(AUTH_REFRESH_KEY)
    set({ token: accessToken, isAuthenticated: true })
  },

  clearAuth: () => {
    sessionStorage.removeItem(AUTH_TOKEN_KEY)
    sessionStorage.removeItem(AUTH_REFRESH_KEY)
    set({ user: null, token: null, isAuthenticated: false, permissions: null, isSuper: false })
  },

  refreshPermissions: async () => {
    // 并发去重：窗口聚焦重取与 fetchUser 内部调用会叠加，不去重会出现慢响应覆盖快响应的权限错乱
    if (_refreshPermsPromise) return _refreshPermsPromise
    _refreshPermsPromise = (async () => {
      try {
        const profile = await apiGet<import('../types').RbacProfile>('/admin/rbac/profile')
        set({ permissions: profile.permissions, isSuper: profile.is_super })
      } catch {
        // 失败**保留**上一次的权限集：清空会让 PermGuard 立刻把当前页换成 403，
        // 编辑教程时一次 502 就把 898 行未保存内容连同 Quill 一起卸载（用户还以为被撤权）。
        // 真正的越权由后端 fail-closed 拦住（接口 403），前端这层只是体验，宁可陈旧不可误杀；
        // 只有从未成功拿到过权限（permissions === null）时才保持空集。
        if (get().permissions === null) set({ isSuper: false })
      } finally {
        _refreshPermsPromise = null
      }
    })()
    return _refreshPermsPromise
  },

  fetchUser: async () => {
    if (_fetchUserPromise) return _fetchUserPromise
    set({ loading: true })
    _fetchUserPromise = (async () => {
      try {
        const user = await apiGet<AdminUser>('/auth/me')
        set({ user, loading: false, isAuthenticated: true })
        // RBAC 档案失败降级为空权限集：菜单不可见比误放行更安全
        await get().refreshPermissions()
        return { kind: 'ok', user } as const
      } catch (err) {
        set({ loading: false })
        if (err && typeof err === 'object' && 'response' in err) {
          const response = (err as { response?: { status?: number; data?: { message?: string } } }).response
          if (response?.status === 401) {
            // 认证失效：清除 token，由调用方跳转登录页
            get().clearAuth()
            return { kind: 'unauthorized' } as const
          }
          if (response?.status) {
            return { kind: 'http', status: response.status, message: response.data?.message || '请求失败' } as const
          }
        }
        // 网络错误：保留 token
        return { kind: 'network' } as const
      } finally {
        _fetchUserPromise = null
      }
    })()
    return _fetchUserPromise
  },
}))
