import { useEffect, useState } from 'react'
import { fetchCategories } from '../utils/categories'

/** 支持分类管理的业务模块 */
type CategoryModule = 'tutorial' | 'resource' | 'node' | 'plugin' | 'tool'

/** 分类下拉选项 hook：加载动态分类，失败时保留静态默认值 */
export function useCategoryOptions(module: CategoryModule, fallback: readonly string[]): string[] {
  const [options, setOptions] = useState<string[]>([...fallback])

  useEffect(() => {
    fetchCategories(module).then(setOptions).catch(() => {})
  }, [module])

  return options
}
