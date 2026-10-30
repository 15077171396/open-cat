/**
 * 分类展示配色（教程 / 百科 / 扩展等模块共用）。
 *
 * 分类取值来自数据库 `categories` 表，运营可自由新增，前端无法穷举；
 * 因此用**名称哈希**稳定取色：同一个分类每次渲染颜色一致，未知分类也有合理配色，
 * 且不需要维护一张「分类 → 颜色」的映射表（新增分类不用改代码）。
 */
export interface CategoryMeta {
  /** 胶囊文字色 */
  text: string
  /** 胶囊底色 */
  bg: string
  /** 无封面时的图标色块渐变 */
  gradient: string
}

const CATEGORY_PALETTE: CategoryMeta[] = [
  {
    text: 'var(--app-primary)',
    bg: 'var(--app-primary-weak)',
    gradient: 'linear-gradient(135deg, var(--app-primary), var(--app-primary-hover))',
  },
  {
    text: 'var(--app-purple)',
    bg: 'var(--app-purple-bg)',
    gradient: 'linear-gradient(135deg, var(--app-purple), #9254de)',
  },
  {
    text: 'var(--app-teal)',
    bg: 'var(--app-teal-bg)',
    gradient: 'linear-gradient(135deg, #13c2c2, #36cfc9)',
  },
  {
    text: 'var(--app-success)',
    bg: 'var(--app-success-bg)',
    gradient: 'linear-gradient(135deg, var(--app-success), #73d13d)',
  },
  {
    text: 'var(--app-warning)',
    bg: 'var(--app-warning-bg)',
    gradient: 'linear-gradient(135deg, var(--app-warning), #ffa940)',
  },
  {
    text: 'var(--app-pink)',
    bg: 'var(--app-pink-bg)',
    gradient: 'linear-gradient(135deg, #eb2f96, #f759ab)',
  },
]

/** 分类名 → 配色（同名恒同色） */
export function categoryMeta(name: string): CategoryMeta {
  let hash = 0
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) % 9973
  return CATEGORY_PALETTE[hash % CATEGORY_PALETTE.length]
}
