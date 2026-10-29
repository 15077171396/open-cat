import { apiGet } from '../api/client'

interface CategoryItem {
  id: number
  name: string
  sort_order: number
}

const cache = new Map<string, CategoryItem[]>()

export async function fetchCategories(module: 'tutorial' | 'resource' | 'node' | 'plugin' | 'tool'): Promise<string[]> {
  if (cache.has(module)) return cache.get(module)!.map(c => c.name)
  const list = (await apiGet<CategoryItem[]>(`/categories/${module}`)) || []
  cache.set(module, list)
  return list.map(c => c.name)
}

export function clearCategoryCache() {
  cache.clear()
}
