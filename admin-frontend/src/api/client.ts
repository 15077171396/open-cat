import axios from 'axios'
import type { AxiosError, AxiosRequestConfig, InternalAxiosRequestConfig } from 'axios'
import { useAuthStore } from '../store/authStore'

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

/** 后端统一响应信封 */
export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

/** 类型化请求：自动解包响应信封直接返回 data；需要访问 code/message 时仍可用 client */
export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.get<unknown, ApiEnvelope<T>>(url, config)
  return res.data
}

export async function apiPost<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.post<unknown, ApiEnvelope<T>>(url, body, config)
  return res.data
}

export async function apiPut<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.put<unknown, ApiEnvelope<T>>(url, body, config)
  return res.data
}

export async function apiPatch<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.patch<unknown, ApiEnvelope<T>>(url, body, config)
  return res.data
}

export async function apiDelete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.delete<unknown, ApiEnvelope<T>>(url, config)
  return res.data
}

/**
 * 生成一个幂等键，配合 `idempotentConfig(key)` 用在「重复执行会改变结果」的写操作上
 * （批量删除、调整积分、群发通知…）。
 *
 * ⚠️ 用法是**一次用户动作一个 key**：在动作开始前生成（例如弹确认框时），
 * 而不是在发请求时生成 —— 每次请求都新生成一个 key，等于没有幂等。
 */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // 老浏览器兜底（后台主要跑在现代浏览器，这里只求不抛错）
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

/** 把幂等键包成请求配置，直接作为 apiPost/apiDelete 的最后一个参数传入 */
export function idempotentConfig(key: string): AxiosRequestConfig {
  return { headers: { 'Idempotency-Key': key } }
}

// 请求拦截：自动携带管理员 JWT
client.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  /**
   * 显式声明界面语言为中文。
   *
   * 后端已支持按 Accept-Language 下发中/英的菜单与权限名（app/core/i18n.py），
   * 但**页面内的业务文案目前仍是中文**。浏览器默认会带自己的 Accept-Language，
   * 英文系统上就会出现「侧边栏英文 + 页面中文」的混搭 —— 比全中文更难用。
   * 等前端文案翻完，这里换成跟随用户设置即可。
   */
  config.headers['Accept-Language'] = 'zh-CN'
  return config
})

// 进行中的刷新请求：并发 401 共享同一次刷新，避免 refresh_token 被并发消耗
let refreshPromise: Promise<boolean> | null = null

// 续期失败跳转登录页的去重标志（并发 401 全部续期失败时只跳一次）
let redirectingToLogin = false

/**
 * 换新 token 对（后端同时轮换 refresh_token 并写回 httpOnly cookie）；成功返回 true。
 *
 * SEC-12：**不再从 sessionStorage 读 refresh_token** —— 它现在只存在于 httpOnly cookie
 * 里（JS 读不到），所以刻意不发请求体，由后端从 cookie 取。
 * 这样即使管理端出现 XSS，攻击者也只能拿到当前 access token（几分钟），
 * 拿不到能续期 7 天的长期凭据。
 */
async function refreshAccessToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshPromise = (async () => {
    try {
      // 用原生 axios 发送，绕开本实例拦截器，避免刷新失败再触发 401 递归
      const res = await axios.post('/api/v1/auth/refresh')
      const data = res.data?.data
      if (!data?.access_token) return false
      useAuthStore.getState().onTokensRefreshed(data.access_token)
      return true
    } catch {
      return false
    } finally {
      refreshPromise = null
    }
  })()
  return refreshPromise
}

// 响应拦截：401 时静默续期并重放原请求（仅一次），续期彻底失败才清态跳登录页
client.interceptors.response.use(
  (response) => response.data,
  async (error: AxiosError) => {
    const status = error.response?.status
    const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined
    // 登录/登出/刷新等认证接口的 401 是业务结果，不应触发续期或清态
    const isAuthEndpoint = !!original?.url && /\/auth\/(login|register|refresh|logout)/.test(original.url)

    if (status === 401 && original && !original._retried && !isAuthEndpoint) {
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        original._retried = true
        return client(original)
      }
      // 无 refresh_token 或刷新失败：清空登录态并整页跳转（编辑中表单将丢失，但该场景已被续期机制压到 7 天后）
      if (useAuthStore.getState().token && !redirectingToLogin) {
        redirectingToLogin = true
        useAuthStore.getState().clearAuth()
        window.location.href = '/admin/login'
      }
    }
    return Promise.reject(error)
  }
)

export default client
