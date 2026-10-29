/** 从 axios 错误中提取用户可读的消息 */
export function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const response = (err as { response?: { data?: { message?: string } } }).response
    if (response?.data?.message) {
      return response.data.message
    }
  }
  if (err instanceof Error && err.message) {
    return err.message
  }
  return fallback
}

/** 判断是否为表单校验错误（非 API 错误） */
export function isFormValidationError(err: unknown): boolean {
  return !!err && typeof err === 'object' && 'errorFields' in err
}

/**
 * 取出后端业务异常里携带的结构化数据（`AppException.data`）。
 *
 * 用途：同一状态码下要区分原因时不能靠匹配 message 文本（文案一改就静默失效），
 * 例如建号 409 —— 「账号已存在」和「账号已注销」处理方式完全不同，
 * 后端在 data.reason 里给判别值（`account_deleted`），前端据此分流。
 */
export function getErrorData<T = Record<string, unknown>>(err: unknown): T | null {
  if (err && typeof err === 'object' && 'response' in err) {
    const data = (err as { response?: { data?: { data?: unknown } } }).response?.data?.data
    if (data && typeof data === 'object') return data as T
  }
  return null
}

/**
 * 取 HTTP 状态码（供「区分 404 与其它失败」使用）。
 *
 * 典型场景：管理端升级后后端还是旧进程 → 新接口一律 404。
 * 这时页面若只说「加载失败」，用户会以为是代码坏了；能拿到 404 就可以直接提示「重启后端」。
 */
export function getApiErrorStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object' && 'response' in err) {
    return (err as { response?: { status?: number } }).response?.status
  }
  return undefined
}
