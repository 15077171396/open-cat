import { apiGet, apiPost } from '../../api/client'

export type MaterialKind = 'image' | 'video' | 'audio' | 'document' | 'archive' | 'other'

export interface MaterialItem {
  id: number
  kind: MaterialKind
  source: string
  title: string
  filename: string
  alt: string
  author: string
  mime_type: string
  width: number
  height: number
  size_bytes: number
  cos_url: string
  thumb_url: string
  license: string
  page_url: string
  usage_count: number
  last_used_at: string
  created_at: string
}

export interface MaterialListResult {
  items: MaterialItem[]
  total: number
  page: number
  page_size: number
}

export interface MaterialStats {
  total: number
  kinds: Record<string, number>
}

export function fetchMaterials(params: {
  keyword?: string
  kind?: string
  source?: string
  page: number
  pageSize: number
}): Promise<MaterialListResult> {
  return apiGet<MaterialListResult>('/admin/materials', {
    params: {
      keyword: params.keyword || undefined,
      kind: params.kind || undefined,
      source: params.source || undefined,
      page: params.page,
      page_size: params.pageSize,
    },
  })
}

export function fetchMaterialStats(): Promise<MaterialStats> {
  return apiGet<MaterialStats>('/admin/materials/stats')
}

export function touchMaterial(id: number): Promise<MaterialItem> {
  return apiPost<MaterialItem>(`/admin/materials/${id}/touch`)
}
