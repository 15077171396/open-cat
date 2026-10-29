/** 图片搜索接口与前端状态的共享类型。 */

export type ImageTypeFilter = 'all' | 'photo' | 'illustration' | 'meme'
export type ImageOrientation = 'all' | 'landscape' | 'portrait' | 'square'

export interface ImageSearchSource {
  id: string
  name: string
  configured: boolean
  license: string
  license_url: string
  requires_attribution: boolean
  types: Array<'photo' | 'illustration' | 'meme'>
  help_url: string
}

export interface ImageSearchOptions {
  enabled: boolean
  page_size: number
  cos_configured: boolean
  local_available: boolean
  sources: ImageSearchSource[]
}

export interface ImageSearchItem {
  source: string
  source_id: string
  thumb_url: string
  preview_url: string
  thumb_proxy_url: string
  preview_proxy_url: string
  width: number
  height: number
  alt: string
  title: string
  author: string
  author_url: string
  page_url: string
  license: string
  license_url: string
  requires_attribution: boolean
}

export interface ImageSearchResult {
  source: string
  items: ImageSearchItem[]
  has_more: boolean
  total: number | null
  cached: boolean
}

export interface ImageCredit {
  text: string
  url: string
  license: string
  license_url: string
  requires_attribution: boolean
}

export interface ImageImportResult {
  url: string
  thumb_url: string | null
  width: number
  height: number
  alt: string
  asset_id: number
  deduped: boolean
  credit: ImageCredit
}

export interface ImageInsertPayload {
  url: string
  alt: string
  credit: ImageCredit | null
}

export interface ImageSearchFilters {
  type: ImageTypeFilter
  orientation: ImageOrientation
}

export interface SourceResultState {
  items: ImageSearchItem[]
  page: number
  hasMore: boolean
  loading: boolean
  error: string
  skipped: boolean
}
