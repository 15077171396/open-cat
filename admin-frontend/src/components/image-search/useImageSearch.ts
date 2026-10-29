/** 图片搜索状态：按图源并发请求，单源失败不影响其他源，支持加载更多与取消。 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getErrorMessage } from '../../utils/error'
import { getImageSearchOptions, searchImages } from './imageSearchApi'
import type {
  ImageSearchFilters, ImageSearchItem, ImageSearchOptions, ImageSearchResult,
  ImageSearchSource, SourceResultState,
} from './types'

const RESULT_CACHE_TTL_MS = 5 * 60 * 1000

const EMPTY_RESULT: SourceResultState = {
  items: [], page: 1, hasMore: false, loading: false, error: '', skipped: false,
}

const LOCAL_SOURCE: ImageSearchSource = {
  id: 'local', name: '本站素材', configured: true, license: '本站上传',
  license_url: '', requires_attribution: false, types: ['photo', 'illustration'],
  help_url: '',
}

function cacheKey(source: string, query: string, filters: ImageSearchFilters, page: number): string {
  return `${source}|${query}|${filters.type}|${filters.orientation}|${page}`
}

function dedupe(items: ImageSearchItem[]): ImageSearchItem[] {
  const seen = new Set<string>()
  return items.filter((item) => {
    const key = `${item.source}:${item.source_id}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function toState(
  data: ImageSearchResult, existing: ImageSearchItem[] = [], page?: number,
): SourceResultState {
  return {
    items: dedupe([...existing, ...data.items]),
    page: page ?? 1,
    hasMore: data.has_more,
    loading: false,
    error: '',
    skipped: false,
  }
}

function supportsType(source: ImageSearchSource, type: ImageSearchFilters['type']): boolean {
  if (type === 'all' || source.id === 'local') return true
  return source.types.includes(type)
}

export function useImageSearch(open: boolean, initialQuery = '') {
  const [options, setOptions] = useState<ImageSearchOptions | null>(null)
  const [optionsLoading, setOptionsLoading] = useState(false)
  const [optionsError, setOptionsError] = useState('')
  const [query, setQuery] = useState(initialQuery)
  const [activeSource, setActiveSource] = useState<'all' | string>('all')
  const [results, setResults] = useState<Record<string, SourceResultState>>({})

  const controllers = useRef(new Map<string, AbortController>())
  const resultCache = useRef(new Map<string, { at: number; data: ImageSearchResult }>())
  const runId = useRef(0)
  const queryRef = useRef(query)

  useEffect(() => {
    queryRef.current = query
  }, [query])

  const abortAll = useCallback(() => {
    controllers.current.forEach((controller) => controller.abort())
    controllers.current.clear()
  }, [])

  const loadOptions = useCallback(async (force = false) => {
    setOptionsLoading(true)
    setOptionsError('')
    try {
      setOptions(await getImageSearchOptions(force))
    } catch (err) {
      setOptionsError(getErrorMessage(err, '图片图源配置加载失败'))
    } finally {
      setOptionsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) {
      abortAll()
      return
    }
    setQuery(initialQuery)
    setActiveSource('all')
    void loadOptions()
    return () => {
      abortAll()
    }
  }, [open, initialQuery, abortAll, loadOptions])

  const sources = useMemo<ImageSearchSource[]>(() => {
    if (!options) return []
    const external = options.sources.filter((item) => item.configured)
    return options.local_available ? [LOCAL_SOURCE, ...external] : external
  }, [options])

  const sourceIds = useMemo(() => sources.map((item) => item.id), [sources])

  useEffect(() => {
    if (activeSource !== 'all' && !sourceIds.includes(activeSource)) {
      setActiveSource('all')
    }
  }, [activeSource, sourceIds])

  const fetchPage = useCallback(async (
    source: string,
    nextQuery: string,
    filters: ImageSearchFilters,
    page: number,
    append: boolean,
    currentRun: number,
  ) => {
    // 本站素材会随上传实时变化，不能复用 5 分钟缓存；外部图源才缓存。
    const cacheable = source !== 'local'
    const key = cacheKey(source, nextQuery, filters, page)
    if (cacheable && !append) {
      const cached = resultCache.current.get(key)
      if (cached && Date.now() - cached.at < RESULT_CACHE_TTL_MS) {
        setResults((prev) => ({ ...prev, [source]: toState(cached.data) }))
        return
      }
    }

    controllers.current.get(source)?.abort()
    const controller = new AbortController()
    controllers.current.set(source, controller)
    setResults((prev) => ({
      ...prev,
      [source]: {
        ...(prev[source] || EMPTY_RESULT),
        loading: true,
        error: '',
        skipped: false,
        ...(append ? {} : { items: [], page: 1, hasMore: false }),
      },
    }))

    try {
      const data = await searchImages({
        source, q: nextQuery, page,
        perPage: options?.page_size || 24,
        filters, signal: controller.signal,
      })
      if (currentRun !== runId.current) return
      if (cacheable) resultCache.current.set(key, { at: Date.now(), data })
      setResults((prev) => ({
        ...prev,
        [source]: toState(data, append ? prev[source]?.items || [] : [], page),
      }))
    } catch (err) {
      if (currentRun !== runId.current || controller.signal.aborted) return
      setResults((prev) => ({
        ...prev,
        [source]: {
          ...(prev[source] || EMPTY_RESULT),
          loading: false,
          error: getErrorMessage(err, '图片加载失败'),
        },
      }))
    } finally {
      if (controllers.current.get(source) === controller) {
        controllers.current.delete(source)
      }
    }
  }, [options?.page_size])

  const search = useCallback(async (nextQuery: string, filters: ImageSearchFilters) => {
    const currentRun = ++runId.current
    abortAll()
    setQuery(nextQuery)
    queryRef.current = nextQuery
    const skipped = sources.filter((item) => !supportsType(item, filters.type))
    if (skipped.length) {
      setResults((prev) => {
        const next = { ...prev }
        for (const source of skipped) next[source.id] = { ...EMPTY_RESULT, skipped: true }
        return next
      })
    }
    const targets = sources.filter((item) => supportsType(item, filters.type))
    await Promise.all(targets.map((source) => {
      if (source.id !== 'local' && !nextQuery.trim()) {
        setResults((prev) => ({ ...prev, [source.id]: { ...EMPTY_RESULT } }))
        return Promise.resolve()
      }
      return fetchPage(source.id, nextQuery, filters, 1, false, currentRun)
    }))
  }, [sources, abortAll, fetchPage])

  const loadMore = useCallback((source: string, filters: ImageSearchFilters) => {
    const state = results[source]
    if (!state || state.loading || !state.hasMore) return Promise.resolve()
    return fetchPage(source, queryRef.current, filters, state.page + 1, true, runId.current)
  }, [results, fetchPage])

  const retry = useCallback((source: string, filters: ImageSearchFilters) => {
    return fetchPage(source, queryRef.current, filters, 1, false, runId.current)
  }, [fetchPage])

  const reloadOptions = useCallback(() => loadOptions(true), [loadOptions])

  return {
    options, optionsLoading, optionsError, reloadOptions,
    sources, query, setQuery, activeSource, setActiveSource,
    results, search, loadMore, retry,
  }
}
