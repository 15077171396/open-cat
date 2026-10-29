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
  { text: '#0958d9', bg: '#e6f4ff', gradient: 'linear-gradient(135deg, #1677ff, #4096ff)' },
  { text: '#531dab', bg: '#f9f0ff', gradient: 'linear-gradient(135deg, #722ed1, #9254de)' },
  { text: '#006d75', bg: '#e6fffb', gradient: 'linear-gradient(135deg, #13c2c2, #36cfc9)' },
  { text: '#389e0d', bg: '#f6ffed', gradient: 'linear-gradient(135deg, #52c41a, #73d13d)' },
  { text: '#d46b08', bg: '#fff7e6', gradient: 'linear-gradient(135deg, #fa8c16, #ffa940)' },
  { text: '#c41d7f', bg: '#fff0f6', gradient: 'linear-gradient(135deg, #eb2f96, #f759ab)' },
]

/** 分类名 → 配色（同名恒同色） */
export function categoryMeta(name: string): CategoryMeta {
  let hash = 0
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) % 9973
  return CATEGORY_PALETTE[hash % CATEGORY_PALETTE.length]
}
