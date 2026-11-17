import { useEffect, useRef, useState } from 'react'
import { fetchPreferences, fromServerPreferences, savePreferences, toServerPreferences } from '../api/preferences'
import { usePrefsStore } from '../store/prefsStore'

/** 修改偏好后的防抖窗口：连续拖拽/点选只发最后一次 */
const SAVE_DEBOUNCE_MS = 800

function serializeCurrent(): string {
  return JSON.stringify(toServerPreferences(usePrefsStore.getState()))
}

/** 立即把当前偏好 PUT 到服务端（重置配置这类「马上要刷新页面」的场景用） */
export async function flushPreferencesNow(): Promise<void> {
  const payload = toServerPreferences(usePrefsStore.getState())
  await savePreferences(payload).catch(() => undefined)
}

/**
 * 界面偏好与锁屏状态的服务端同步（启动拉取 + 防抖回写）。
 *
 * 设计取舍：
 * - **不阻塞首屏**：首屏完全用 localStorage 渲染，拉取在后台进行；成功后服务端值覆盖本地；
 * - **失败静默**：离线/接口异常不弹错误，继续用本地值（下次有网再说）；
 * - **服务端优先**：只有 `exists=true` 才合并，避免「从没保存过」被默认值冲掉本地选择。
 */
export function usePreferencesSync() {
  const [ready, setReady] = useState(false)
  const [hasLockPassword, setHasLockPassword] = useState(false)
  // 最近一次已知与服务端一致（或已发出）的序列化值，用来跳过无意义的 PUT
  const lastSavedRef = useRef('')

  useEffect(() => {
    let alive = true
    const pull = async () => {
      try {
        const data = await fetchPreferences()
        if (!alive) return
        setHasLockPassword(data.has_lock_password)
        if (data.exists && data.preferences) {
          usePrefsStore.getState().applyServerPrefs(fromServerPreferences(data.preferences))
        }
      } catch {
        // 静默降级：首屏与本地持久化不受影响
      } finally {
        if (alive) {
          lastSavedRef.current = serializeCurrent()
          setReady(true)
        }
      }
    }
    void pull()
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!ready) return
    let timer: ReturnType<typeof setTimeout> | undefined

    const flush = () => {
      const payload = toServerPreferences(usePrefsStore.getState())
      const raw = JSON.stringify(payload)
      if (raw === lastSavedRef.current) return
      lastSavedRef.current = raw
      void savePreferences(payload).catch(() => {
        // 失败静默：本地已经持久化，下次改动会再尝试
      })
    }

    const unsubscribe = usePrefsStore.subscribe(() => {
      if (serializeCurrent() === lastSavedRef.current) return
      if (timer) clearTimeout(timer)
      timer = setTimeout(flush, SAVE_DEBOUNCE_MS)
    })

    // 关页/切后台时尽力把最后一次变更发出去；失败也无所谓（下次启动以服务端为准）
    const onPageHide = () => {
      if (timer) {
        clearTimeout(timer)
        timer = undefined
        flush()
      }
    }
    window.addEventListener('pagehide', onPageHide)
    return () => {
      unsubscribe()
      window.removeEventListener('pagehide', onPageHide)
      if (timer) clearTimeout(timer)
    }
  }, [ready])

  return {
    ready,
    hasLockPassword,
    /** 设置锁屏口令成功后同步本地标志，避免下一次点击又弹设置框 */
    markLockPasswordSet: () => setHasLockPassword(true),
  }
}
