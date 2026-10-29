import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getImageSearchOptions, searchImages } from './imageSearchApi'
import { useImageSearch } from './useImageSearch'
import type { ImageSearchItem, ImageSearchOptions, ImageSearchResult } from './types'

vi.mock('./imageSearchApi', () => ({
  getImageSearchOptions: vi.fn(),
  searchImages: vi.fn(),
}))

const options: ImageSearchOptions = {
  enabled: true,
  page_size: 12,
  cos_configured: true,
  local_available: true,
  sources: [
    {
      id: 'pexels', name: 'Pexels', configured: true, license: 'Pexels License',
      license_url: '', requires_attribution: false, types: ['photo'], help_url: '',
    },
    {
      id: 'pixabay', name: 'Pixabay', configured: true, license: 'Pixabay Content License',
      license_url: '', requires_attribution: true, types: ['photo', 'illustration'],
      help_url: '',
    },
    {
      id: 'lummi', name: 'Lummi', configured: false, license: '', license_url: '',
      requires_attribution: true, types: ['illustration'], help_url: '',
    },
  ],
}

const filters = { type: 'all' as const, orientation: 'all' as const }

function item(source: string, id: string): ImageSearchItem {
  return {
    source, source_id: id, thumb_url: '', preview_url: '',
    thumb_proxy_url: '', preview_proxy_url: '', width: 100, height: 100,
    alt: '', title: '', author: '', author_url: '', page_url: '',
    license: '', license_url: '', requires_attribution: false,
  }
}

function result(source: string, items: ImageSearchItem[], hasMore = false): ImageSearchResult {
  return { source, items, has_more: hasMore, total: items.length, cached: false }
}

async function setup() {
  const hook = renderHook(() => useImageSearch(true, ''))
  await waitFor(() => expect(hook.result.current.sources).toHaveLength(3))
  return hook
}

describe('useImageSearch', () => {
  beforeEach(() => {
    vi.mocked(getImageSearchOptions).mockReset()
    vi.mocked(searchImages).mockReset()
    vi.mocked(getImageSearchOptions).mockResolvedValue(options)
  })

  it('loads options and requests each configured source in parallel', async () => {
    vi.mocked(searchImages).mockImplementation(async ({ source }) => {
      if (source === 'pexels') return result('pexels', [item('pexels', 'p1')])
      if (source === 'pixabay') return result('pixabay', [item('pixabay', 'b1')])
      return result('local', [])
    })
    const { result: hook } = await setup()

    await act(async () => {
      await hook.current.search('cat', filters)
    })

    expect(searchImages).toHaveBeenCalledTimes(3)
    expect(hook.current.sources.map((s) => s.id)).toEqual(['local', 'pexels', 'pixabay'])
    expect(hook.current.results.pexels.items[0].source_id).toBe('p1')
    expect(hook.current.results.pixabay.items[0].source_id).toBe('b1')
  })

  it('keeps other sources when one fails', async () => {
    vi.mocked(searchImages).mockImplementation(async ({ source }) => {
      if (source === 'pixabay') throw new Error('Pixabay 超时')
      if (source === 'pexels') return result('pexels', [item('pexels', 'p1')])
      return result('local', [])
    })
    const { result: hook } = await setup()
    await act(async () => {
      await hook.current.search('cat', filters)
    })

    expect(hook.current.results.pexels.items).toHaveLength(1)
    expect(hook.current.results.pixabay.error).toBe('Pixabay 超时')
    expect(hook.current.results.pixabay.loading).toBe(false)
  })

  it('skips sources that do not support the selected type', async () => {
    vi.mocked(searchImages).mockImplementation(async ({ source }) => result(source, []))
    const { result: hook } = await setup()
    await act(async () => {
      await hook.current.search('cat', { type: 'illustration', orientation: 'all' })
    })

    expect(hook.current.results.pexels.skipped).toBe(true)
    expect(searchImages).toHaveBeenCalledTimes(2)
  })

  it('loads more and dedupes by source and id', async () => {
    let page = 1
    vi.mocked(searchImages).mockImplementation(async ({ source, page: requested }) => {
      if (source !== 'pexels') return result(source, [])
      page = requested
      if (requested === 1) return result('pexels', [item('pexels', 'p1')], true)
      return result('pexels', [item('pexels', 'p1'), item('pexels', 'p2')], false)
    })
    const { result: hook } = await setup()
    await act(async () => {
      await hook.current.search('cat', filters)
    })
    expect(hook.current.results.pexels.hasMore).toBe(true)

    await act(async () => {
      await hook.current.loadMore('pexels', filters)
    })
    expect(page).toBe(2)
    expect(hook.current.results.pexels.items.map((i) => i.source_id)).toEqual(['p1', 'p2'])
    expect(hook.current.results.pexels.hasMore).toBe(false)
  })

  it('aborts the previous request when a new search starts', async () => {
    const signals: AbortSignal[] = []
    vi.mocked(searchImages).mockImplementation(({ signal }) => {
      if (signal) signals.push(signal)
      return new Promise(() => {}) as Promise<ImageSearchResult>
    })
    const { result: hook } = await setup()
    act(() => {
      void hook.current.search('cat', filters)
    })
    act(() => {
      void hook.current.search('dog', filters)
    })
    expect(signals.length).toBeGreaterThan(0)
    expect(signals[0].aborted).toBe(true)
  })

  it('only loads local assets when query is empty', async () => {
    vi.mocked(searchImages).mockImplementation(async ({ source }) => result(source, []))
    const { result: hook } = await setup()
    await act(async () => {
      await hook.current.search('', filters)
    })
    expect(searchImages).toHaveBeenCalledTimes(1)
    expect(vi.mocked(searchImages).mock.calls[0][0].source).toBe('local')
  })

  it('does not cache local assets because uploads change the library', async () => {
    vi.mocked(searchImages).mockImplementation(async ({ source }) => result(source, []))
    const { result: hook } = await setup()
    await act(async () => {
      await hook.current.search('', filters)
    })
    await act(async () => {
      await hook.current.search('', filters)
    })
    expect(searchImages).toHaveBeenCalledTimes(2)
  })
})
