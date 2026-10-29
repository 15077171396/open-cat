import client from '../api/client'

/** 图片大小上限 (5MB)，与后端 /uploads/image 一致 */
const IMAGE_MAX_SIZE = 5 * 1024 * 1024
/** 通用文件大小上限 (20MB)，超出需使用第三方网盘 */
const FILE_MAX_SIZE = 20 * 1024 * 1024
/** 允许上传的图片 MIME 类型 */
const IMAGE_ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']

/** COS 上传响应数据 */
interface UploadResult {
  url: string
  filename?: string
  size?: number
}

interface UploadOptions {
  /** 文件大小上限（字节） */
  maxSize: number
  /** 允许的 MIME 类型白名单，不传则不校验类型 */
  allowedTypes?: string[]
  /** 请求超时（ms），默认 60s */
  timeout?: number
  /** 大小超限时的提示文案 */
  sizeErrorMessage?: string
  /** 类型不匹配时的提示文案 */
  typeErrorMessage?: string
}

/** 上传文件到后端 COS 接口，统一封装类型/大小校验与 FormData 提交；失败抛错由调用方提示 */
async function uploadToCos(
  file: File,
  endpoint: '/uploads/image' | '/uploads/file',
  options: UploadOptions,
): Promise<UploadResult> {
  const { maxSize, allowedTypes, timeout = 60000, sizeErrorMessage, typeErrorMessage } = options

  if (allowedTypes && !allowedTypes.includes(file.type)) {
    throw new Error(typeErrorMessage || '不支持的文件格式')
  }
  if (file.size > maxSize) {
    const mb = Math.round(maxSize / 1024 / 1024)
    throw new Error(sizeErrorMessage || `文件大小不能超过 ${mb}MB`)
  }

  const formData = new FormData()
  formData.append('file', file)
  const res = await client.post<unknown, { code: number; message: string; data: UploadResult }>(
    endpoint,
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout },
  )
  if (res.code !== 0 || !res.data?.url) {
    throw new Error(res.message || '上传失败')
  }
  return res.data
}

/** 上传图片到 COS（5MB 限制，校验图片格式），供 QuillEditor 与封面图复用 */
export function uploadImage(file: File): Promise<UploadResult> {
  return uploadToCos(file, '/uploads/image', {
    maxSize: IMAGE_MAX_SIZE,
    allowedTypes: IMAGE_ALLOWED_TYPES,
    typeErrorMessage: '仅支持 JPG/PNG/WEBP/GIF 格式图片',
    sizeErrorMessage: '图片大小不能超过 5MB',
  })
}

/** 上传资源文件到 COS（20MB 限制），超限提示使用第三方网盘 */
export function uploadResourceFile(file: File): Promise<UploadResult> {
  return uploadToCos(file, '/uploads/file', {
    maxSize: FILE_MAX_SIZE,
    timeout: 120000,
    sizeErrorMessage: '文件超过 20MB 限制，请使用第三方网盘服务（如夸克网盘、百度网盘等）存储，并在下方网盘链接中填写',
  })
}

/**
 * 头像上传前的客户端压缩（与用户端 `Settings.tsx::compressAvatar` 同一策略）。
 *
 * 后端 `/users/avatar` 的上限是 `AVATAR_MAX_SIZE`，**默认 2MB**（后台可调 1–20MB），
 * 而这里的 `IMAGE_MAX_SIZE` 是给封面图 `/uploads/image` 用的 5MB —— 拿它当头像门槛
 * 会出现「前端放行、后端 400」的错配。统一缩到长边 ≤512px 的 WebP（保留透明通道），
 * 通常 20–80KB，与后端配置解耦。解码失败原样返回，交给后端判定。
 */
async function compressAvatar(file: File): Promise<File> {
  if (file.size <= 512 * 1024) return file
  if (file.type === 'image/gif') return file
  if (typeof createImageBitmap !== 'function') return file
  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, 512 / Math.max(bitmap.width, bitmap.height))
    const width = Math.max(1, Math.round(bitmap.width * scale))
    const height = Math.max(1, Math.round(bitmap.height * scale))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      bitmap.close()
      return file
    }
    ctx.drawImage(bitmap, 0, 0, width, height)
    bitmap.close()
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/webp', 0.9)
    )
    if (!blob) return file
    // 同上：以 blob 真实类型为准（Safari 13- 不支持 WebP 编码会回落成 PNG）
    const outType = blob.type || 'image/webp'
    const ext = outType === 'image/png' ? '.png' : outType === 'image/jpeg' ? '.jpg' : '.webp'
    const base = file.name.replace(/\.[^.]+$/, '') || 'avatar'
    return new File([blob], `${base}${ext}`, { type: outType })
  } catch {
    return file
  }
}

/** 头像上传响应数据（后端 /users/avatar 直接更新 user.avatar_url） */
interface AvatarUploadResult {
  avatar_url: string
}

/**
 * 上传头像（2026-10-13 个人中心）。
 *
 * 与 `uploadImage` 的区别：头像走**用户自助接口** `/users/avatar`（字段名 `avatar`），
 * 后端落库并返回新地址，所以调用方拿到结果后只需刷新用户信息即可，
 * 不像封面图那样要自己再 PUT 一次。
 *
 * 魔数校验不在这里做 —— 后端 `upload_avatar` 已用 `is_allowed_image` 校验文件头，
 * 前端重复实现只会多一份会漂移的副本。
 */
export async function uploadAvatar(file: File): Promise<AvatarUploadResult> {
  if (!IMAGE_ALLOWED_TYPES.includes(file.type)) {
    throw new Error('仅支持 JPG/PNG/WEBP/GIF 格式图片')
  }
  if (file.size > IMAGE_MAX_SIZE) {
    throw new Error(`头像大小不能超过 ${Math.round(IMAGE_MAX_SIZE / 1024 / 1024)}MB`)
  }
  // 压缩后再传：手机/相机原图常 2–5MB，会撞后端 2MB 的头像上限
  const payload = await compressAvatar(file)
  const formData = new FormData()
  formData.append('avatar', payload)
  const res = await client.post<unknown, { code: number; message: string; data: AvatarUploadResult }>(
    '/users/avatar',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60000 },
  )
  if (res.code !== 0 || !res.data?.avatar_url) {
    throw new Error(res.message || '头像上传失败')
  }
  return res.data
}
