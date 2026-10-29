import { useState } from 'react'
import type { ReactNode } from 'react'

interface ContentThumbProps {
  /** 封面图 URL；为空或加载失败时回落到图标色块 */
  cover?: string | null
  /** 回落色块的背景（各模块按类型/分类给不同渐变） */
  gradient: string
  /** 回落色块里的图标 */
  icon: ReactNode
}

/**
 * 后台列表首列的封面缩略图（资源 / 教程 / 百科 / 扩展共用）。
 *
 * 无封面、或图片加载失败（COS 上被删、URL 写错）时回落到「类型图标色块」，
 * 而不是显示一个破图 —— 与前台卡片「未上传封面时用类型图标底色兜底」的约定一致。
 */
export default function ContentThumb({ cover, gradient, icon }: ContentThumbProps) {
  const [broken, setBroken] = useState(false)

  if (cover && !broken) {
    return <img className="adm-thumb" src={cover} alt="" loading="lazy" onError={() => setBroken(true)} />
  }
  return (
    <span className="adm-thumb adm-thumb--icon" style={{ background: gradient }} aria-hidden>
      {icon}
    </span>
  )
}
