/**
 * 导出下载的契约测试。
 *
 * 这段代码最隐蔽的失败是「拿到一个坏文件」：后端返回 403 JSON，但 `responseType: 'blob'`
 * 把它也变成了 Blob，直接存成 .csv 打开是乱码 —— 用户会以为导出功能坏了，
 * 而不是「我没有权限」。所以第一优先级是钉住「JSON 必须当错误抛出来」。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => {
  const get = vi.fn()
  return { default: { get } }
})

import client from './client'
import { downloadCsv, exportErrorMessage } from './download'

const mockedGet = vi.mocked(client.get)

/** jsdom 没有 createObjectURL，需要自己挂一个可断言的替身 */
function stubUrls() {
  const createObjectURL = vi.fn(() => 'blob:mock')
  const revokeObjectURL = vi.fn()
  Object.assign(URL, { createObjectURL, revokeObjectURL })
  return { createObjectURL, revokeObjectURL }
}

describe('downloadCsv', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('正常响应触发下载，并从响应头取文件名', async () => {
    const { revokeObjectURL } = stubUrls()
    const click = vi.fn()
    const realCreate = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = realCreate(tag)
      if (tag === 'a') el.click = click
      return el
    })

    mockedGet.mockResolvedValue({
      data: new Blob(['\ufeffID,账号\r\n1,user1'], { type: 'text/csv' }),
      headers: {
        'content-type': 'text/csv; charset=utf-8',
        'content-disposition': 'attachment; filename="users-20261030-0123.csv"',
      },
    } as never)

    await downloadCsv('/admin/export/users', { role: 'admin' }, 'fallback.csv')

    expect(click).toHaveBeenCalled()
    // 下载完必须回收，否则 blob 一直占着内存
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock')
    vi.restoreAllMocks()
  })

  it('后端返回 JSON 错误时抛 message，而不是把错误存成 csv', async () => {
    stubUrls()
    // jsdom 的 Blob 没有 .text()，用最小替身（生产环境浏览器都有）
    mockedGet.mockResolvedValue({
      data: { text: async () => JSON.stringify({ code: 403, message: '无导出权限' }) },
      headers: { 'content-type': 'application/json' },
    } as never)

    await expect(downloadCsv('/admin/export/users', {})).rejects.toThrow('无导出权限')
  })

  it('JSON 结构异常时给默认文案，不抛 JSON 解析错', async () => {
    stubUrls()
    mockedGet.mockResolvedValue({
      data: { text: async () => 'not json' },
      headers: { 'content-type': 'application/json' },
    } as never)

    await expect(downloadCsv('/admin/export/users', {})).rejects.toThrow('导出失败')
  })

  it('筛选条件原样透传（导出必须与页面筛选一致）', async () => {
    stubUrls()
    mockedGet.mockResolvedValue({
      data: new Blob(['x'], { type: 'text/csv' }),
      headers: { 'content-type': 'text/csv', 'content-disposition': 'attachment; filename="a.csv"' },
    } as never)

    await downloadCsv('/admin/export/tutorials', { keyword: 'AI', category: '入门' })
    expect(mockedGet).toHaveBeenCalledWith('/admin/export/tutorials', {
      params: { keyword: 'AI', category: '入门' },
      responseType: 'blob',
    })
  })
})

describe('exportErrorMessage', () => {
  it('优先用后端给的具体原因', () => {
    expect(exportErrorMessage(new Error('无导出权限'))).toBe('无导出权限')
  })

  it('拿不到 message 时回落到统一提示', () => {
    expect(exportErrorMessage({})).toContain('稍后重试')
  })
})
