import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPost } from '../../api/client'
import {
  getImageSearchOptions, importImage, resetImageSearchOptionsCache, searchImages,
} from './imageSearchApi'

vi.mock('../../api/client', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}))

const options = {
  enabled: true, page_size: 24, cos_configured: true, local_available: true,
  sources: [],
}

describe('imageSearchApi', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
    resetImageSearchOptionsCache()
  })

  it('caches options and shares the in-flight request', async () => {
    vi.mocked(apiGet).mockResolvedValue(options)
    const [a, b] = await Promise.all([
      getImageSearchOptions(),
      getImageSearchOptions(),
    ])
    expect(a).toBe(b)
    expect(apiGet).toHaveBeenCalledTimes(1)
    await getImageSearchOptions()
    expect(apiGet).toHaveBeenCalledTimes(1)
    await getImageSearchOptions(true)
    expect(apiGet).toHaveBeenCalledTimes(2)
  })

  it('builds search params', async () => {
    vi.mocked(apiGet).mockResolvedValue({ source: 'pexels', items: [], has_more: false, total: 0, cached: false })
    await searchImages({
      source: 'pexels', q: 'cat', page: 2, perPage: 12,
      filters: { type: 'photo', orientation: 'landscape' },
    })
    expect(apiGet).toHaveBeenCalledWith('/admin/image-search/search', expect.objectContaining({
      params: expect.objectContaining({
        source: 'pexels', q: 'cat', page: 2, per_page: 12,
        type: 'photo', orientation: 'landscape',
      }),
    }))
  })

  it('posts import payload', async () => {
    vi.mocked(apiPost).mockResolvedValue({ url: 'https://cos.example/a.webp' })
    await importImage({ source: 'pexels', sourceId: '1', alt: '猫' })
    expect(apiPost).toHaveBeenCalledWith('/admin/image-search/import', {
      source: 'pexels', source_id: '1', alt: '猫', query: undefined,
    })
  })
})
