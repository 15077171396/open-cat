import { Input, Modal } from 'antd'
import { apiPost } from '../api/client'
import { getErrorMessage } from './error'

/**
 * 危险操作二次校验（S5，2026-11-20）：吊销令牌 / 删除 Webhook 等不可逆动作前，
 * 重新输入登录密码，换一张 5 分钟短时凭据（后端 POST /admin/elevation）。
 *
 * 用法：
 *   const token = await ensureElevation()
 *   await apiDelete(url, { headers: { 'X-Elevation-Token': token } })
 *
 * 5 分钟窗口内连续做一组危险操作只输一次密码（本地缓存，留 30 秒余量防临界）。
 * 取消 / 密码错误 → reject，调用方按普通失败 toast；403 过期时清缓存重新索要。
 */

const CACHE_KEY = 'opencat_elevation'
const LOCAL_TTL_MS = 4.5 * 60 * 1000 // 后端窗口 5 分钟，本地留余量

interface CachedToken {
  token: string
  grantedAt: number
}

function cachedToken(): string | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as CachedToken
    if (!parsed.token || Date.now() - parsed.grantedAt > LOCAL_TTL_MS) {
      localStorage.removeItem(CACHE_KEY)
      return null
    }
    return parsed.token
  } catch {
    return null
  }
}

function promptPassword(): Promise<string> {
  return new Promise((resolve, reject) => {
    let password = ''
    Modal.confirm({
      title: '危险操作 —— 请重新输入登录密码',
      content: (
        <Input.Password
          autoFocus
          placeholder="输入登录密码，确认是本人操作"
          onChange={(e) => {
            password = e.target.value
          }}
        />
      ),
      okText: '确认是本人',
      cancelText: '取消',
      onOk: async () => {
        if (!password) {
          throw new Error('请输入密码')
        }
        try {
          const data = await apiPost<{ token: string; expires_in: number }>('/admin/elevation', {
            password,
          })
          localStorage.setItem(CACHE_KEY, JSON.stringify({ token: data.token, grantedAt: Date.now() }))
          resolve(data.token)
        } catch (error) {
          throw new Error(getErrorMessage(error, '密码校验未通过'))
        }
      },
      onCancel: () => reject(new Error('已取消：该操作需要密码二次校验')),
    })
  })
}

/** 拿到（必要时索要）短时凭据；失败或取消会 reject，调用方 catch 后 toast。 */
export async function ensureElevation(): Promise<string> {
  return cachedToken() ?? (await promptPassword())
}

/** 403（凭据过期）时清缓存，让下一次操作重新索要密码。 */
export function invalidateElevation(): void {
  localStorage.removeItem(CACHE_KEY)
}
