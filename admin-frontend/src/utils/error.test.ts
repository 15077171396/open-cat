import { describe, expect, it } from 'vitest'

import { getErrorData, getErrorMessage, isFormValidationError } from './error'

/**
 * `getErrorData` 存在的理由：同一个状态码下要区分原因时**不能匹配 message 文本**
 * （文案改一个字就静默失效）。后端在 `AppException.data` 里给判别值，
 * 例如建号 409 的 `reason=account_deleted`（→ 引导去恢复）与普通重名。
 */

/** 构造 axios 风格的错误对象（后端统一信封：{code, message, data}） */
function apiError(status: number, message: string, data?: unknown) {
  return { response: { status, data: { code: status, message, data } } }
}

describe('getErrorData', () => {
  it('取出后端业务数据', () => {
    const err = apiError(409, '该账号已被注销，不能复用', {
      reason: 'account_deleted',
      user_id: 9,
    })
    expect(getErrorData<{ reason: string }>(err)?.reason).toBe('account_deleted')
  })

  it('没有 data 时返回 null（普通重名走这条）', () => {
    expect(getErrorData(apiError(409, '该账号已存在'))).toBeNull()
  })

  it('data 为 null 或非对象时返回 null，不抛错', () => {
    expect(getErrorData(apiError(400, 'x', null))).toBeNull()
    expect(getErrorData(apiError(400, 'x', 'oops'))).toBeNull()
  })

  it('非 axios 错误返回 null', () => {
    expect(getErrorData(new Error('boom'))).toBeNull()
    expect(getErrorData(null)).toBeNull()
    expect(getErrorData('string error')).toBeNull()
  })
})

describe('getErrorMessage', () => {
  it('优先用后端 message', () => {
    expect(getErrorMessage(apiError(400, '账号格式不正确'), '兜底')).toBe('账号格式不正确')
  })

  it('无后端 message 时回落 Error.message，再回落 fallback', () => {
    expect(getErrorMessage(new Error('network'), '兜底')).toBe('network')
    expect(getErrorMessage(null, '兜底')).toBe('兜底')
  })
})

describe('isFormValidationError', () => {
  it('只有带 errorFields 的才是表单校验错误', () => {
    expect(isFormValidationError({ errorFields: [] })).toBe(true)
    expect(isFormValidationError(apiError(400, 'x'))).toBe(false)
  })
})
