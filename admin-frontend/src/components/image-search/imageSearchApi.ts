/** 图片搜索 API 客户端；options 在模块级缓存 5 分钟，多个编辑器共用。 */

import { apiGet, apiPost } from '../../api/client'
import type {
  ImageImportResult, ImageSearchFilters, ImageSearchOptions, ImageSearchResult,
} from './types'

const OPTIONS_TTL_MS = 5 * 60 * 1000

let cachedOptions: ImageSearchOptions | null = null
let cachedAt = 0
let optionsInflight: Promise<ImageSearchOptions> | null = null

export async function getImageSearchOptions(force = false): Promise<ImageSearchOptions> {
  const fresh = cachedOptions && Date.now() - cachedAt < OPTIONS_TTL_MS
  if (!force && fresh) return cachedOptions as ImageSearchOptions
  if (!force && optionsInflight) return optionsInflight
  optionsInflight = apiGet<ImageSearchOptions>('/admin/image-search/options')
    .then((data) => {
      cachedOptions = data
      cachedAt = Date.now()
      return data
    })
    .finally(() => {
      optionsInflight = null
    })
  return optionsInflight
}

export function resetImageSearchOptionsCache(): void {
  cachedOptions = null
  cachedAt = 0
  optionsInflight = null
}

export async function searchImages(params: {
  source: string
  q: string
  page: number
  perPage: number
  filters: ImageSearchFilters
  signal?: AbortSignal
}): Promise<ImageSearchResult> {
  return apiGet<ImageSearchResult>('/admin/image-search/search', {
    params: {
      source: params.source,
      q: params.q,
      page: params.page,
      per_page: params.perPage,
      type: params.filters.type,
      orientation: params.filters.orientation,
    },
    signal: params.signal,
  })
}

export async function touchMaterial(assetId: number): Promise<void> {
  await apiPost(`/admin/materials/${assetId}/touch`)
}

export async function importImage(params: {
  source: string
  sourceId: string
  alt?: string
  query?: string
}): Promise<ImageImportResult> {
  return apiPost<ImageImportResult>('/admin/image-search/import', {
    source: params.source,
    source_id: params.sourceId,
    alt: params.alt,
    query: params.query,
  })
}
