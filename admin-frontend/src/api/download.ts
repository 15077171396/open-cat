import client from './client'
import { getErrorMessage } from '../utils/error'

/**
 * 下载 CSV（导出）。
 *
 * 为什么不能 `window.open(url)`：鉴权走 `Authorization: Bearer`（token 在内存/localStorage），
 * 新窗口的请求不会带上它，只会拿到 401。所以必须用 axios 取 blob 再触发下载。
 *
 * 两个容易踩的坑：
 * 1. 后端失败时返回的是 **JSON 信封**，但 `responseType: 'blob'` 会让它也变成 Blob ——
 *    直接把 Blob 存成 .csv，用户会得到一个打开是乱码的文件，还以为是导出功能坏了。
 *    这里检测 content-type 是 JSON 就解析出 `message` 抛出去。
 * 2. 文件名在 `Content-Disposition` 里，且后端已经带了时间戳；
 *    取不到就回落到调用方给的默认名，避免下载成 `download`。
 */
export async function downloadFile(
  url: string,
  params: Record<string, unknown>,
  fallbackName = 'export.csv',
): Promise<void> {
  const res = await client.get(url, { params, responseType: 'blob' })

  const contentType = String(res.headers['content-type'] ?? '')
  const blob = res.data as Blob

  if (contentType.includes('application/json')) {
    // 把错误信封读出来，让用户看到「权限不足」这类真实原因，而不是一个坏文件
    const text = await blob.text()
    let message = '导出失败'
    try {
      const parsed = JSON.parse(text) as { message?: string }
      if (parsed.message) message = parsed.message
    } catch {
      // 不是合法 JSON 就沿用默认文案
    }
    throw new Error(message)
  }

  const disposition = String(res.headers['content-disposition'] ?? '')
  const matched = /filename="?([^";]+)"?/i.exec(disposition)
  const filename = matched?.[1] ?? fallbackName

  const objectUrl = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename
    // 必须挂到文档里再点：Firefox 下游离节点不会触发下载
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    // 不 revoke 会一直占着内存，直到页面刷新
    URL.revokeObjectURL(objectUrl)
  }
}

/** 导出失败时的统一提示（把异常转成给用户看的一句话）。 */
/** 兼容导出按钮的旧名。 */
export const downloadCsv = downloadFile

export function exportErrorMessage(err: unknown): string {
  // 后端在 JSON 信封里给了 message 时，它比任何前端文案都准确（例如「无导出权限」）
  if (err instanceof Error && err.message) {
    return err.message
  }
  return getErrorMessage(err, '导出失败，请稍后重试')
}
