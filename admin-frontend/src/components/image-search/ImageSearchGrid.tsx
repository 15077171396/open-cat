/** 按图源分区展示搜索结果；单源加载/失败/跳过互相独立。
 *
 * 「搜不回来」的图片分两种情况，都在这里收口：
 *  1. 图片请求失败（onError）——逐个候选地址重试，全失败则隐藏卡片；
 *  2. 图片请求挂起（既不 load 也不 error，常见于防盗链/超时）——进入视口后
 *     计时兜底，超时视为不可用，同样隐藏。
 * 图源本身搜不到结果或报错时，也不再用大块空态/错误区占版面：
 * 无结果图源在「全部」视图下直接折叠，只留一行汇总说明。
 */

import { Button, Empty } from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ImageSearchItem, ImageSearchSource, SourceResultState } from './types'

interface Props {
  sources: ImageSearchSource[]
  results: Record<string, SourceResultState>
  activeSource: string
  selectedKey: string
  query: string
  onSelect: (item: ImageSearchItem) => void
  onRetry: (source: string) => void
  onLoadMore: (source: string) => void
}

const SKELETONS = Array.from({ length: 8 }, (_, index) => index)

/** 图片进入视口后仍没有任何 load/error 回调，超过该时长即判定为「搜不回来」。
 *  按候选地址逐个计，缩略图 + 预览图两个候选最坏 10s 出结论。 */
const LOAD_TIMEOUT_MS = 5000

const EMPTY_RESULT: SourceResultState = {
  items: [], page: 1, hasMore: false, loading: false, error: '', skipped: false,
}

function itemKey(item: ImageSearchItem): string {
  return `${item.source}:${item.source_id}`
}

function Card({
  item, selected, onSelect, onUnavailable,
}: {
  item: ImageSearchItem
  selected: boolean
  onSelect: () => void
  onUnavailable: (key: string) => void
}) {
  // 外部直链会绕过代理的占位图检查；回退也只能使用已签名的代理地址。
  const candidates = useMemo(() => [...new Set((item.source === 'local'
    ? [item.thumb_url, item.preview_url]
    : [item.thumb_proxy_url, item.preview_proxy_url]
  ).filter(Boolean))], [item.source, item.thumb_url, item.preview_url, item.thumb_proxy_url, item.preview_proxy_url])
  const [candidateIndex, setCandidateIndex] = useState(0)
  const [loaded, setLoaded] = useState(false)
  // 没有 IntersectionObserver（老浏览器 / 测试环境）时直接当作已进入视口，
  // 避免多一次渲染来切换状态。
  const [inView, setInView] = useState(() => typeof IntersectionObserver === 'undefined')
  const imgRef = useRef<HTMLImageElement>(null)
  const cardRef = useRef<HTMLButtonElement>(null)
  const attempted = useRef(new Set<string>())
  const key = itemKey(item)
  const src = candidates[candidateIndex]

  // 图片是 loading="lazy" 的：没进视口就不会发请求，此时不能开始计时，
  // 否则往下滚之前就会把整屏图片误判成不可用。
  useEffect(() => {
    if (inView) return
    const node = cardRef.current
    if (!node) return
    if (typeof IntersectionObserver === 'undefined') {
      setInView(true)
      return
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setInView(true)
        observer.disconnect()
      }
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [inView])
  const advance = useCallback(() => {
    if (attempted.current.has(src)) return
    attempted.current.add(src)
    setLoaded(false)
    setCandidateIndex(candidateIndex + 1)
    if (candidateIndex + 1 >= candidates.length) onUnavailable(key)
  }, [src, candidateIndex, candidates.length, key, onUnavailable])

  useEffect(() => {
    if (loaded || !inView) return
    // 命中浏览器缓存的 502 图片时不会再触发 error 事件；挂载后主动检查一次。
    const checkTimer = setTimeout(() => {
      const image = imgRef.current
      if (!src) onUnavailable(key)
      else if (image?.complete) {
        if (image.naturalWidth > 0) setLoaded(true)
        else advance()
      }
    }, 0)
    const timeoutTimer = setTimeout(() => {
      const image = imgRef.current
      if (image?.complete && image.naturalWidth > 0) {
        setLoaded(true)
        return
      }
      advance()
    }, LOAD_TIMEOUT_MS)
    return () => {
      clearTimeout(checkTimer)
      clearTimeout(timeoutTimer)
    }
  }, [loaded, inView, src, advance, key, onUnavailable])

  if (!src) return null
  return (
    <button
      ref={cardRef}
      type="button"
      className={`image-search-card${selected ? ' is-selected' : ''}`}
      onClick={onSelect}
      aria-label={item.alt || item.title || '选择图片'}
      aria-pressed={selected}
      data-key={key}
    >
      {!loaded ? <span className="image-search-card__loading" aria-hidden="true" /> : null}
      <img
        ref={imgRef}
        src={src}
        alt={item.alt || item.title || ''}
        loading="lazy"
        decoding="async"
        className={loaded ? 'is-loaded' : 'is-loading'}
        onLoad={() => setLoaded(true)}
        onError={advance}
      />
      <span className="image-search-card__meta">
        <span>{item.author || '未知作者'}</span>
        <span>{item.source === 'local' ? '本站' : item.source}</span>
      </span>
    </button>
  )
}

function Section({
  source, result, visibleItems, hiddenCount, single, selectedKey, query,
  onSelect, onRetry, onLoadMore, onUnavailable,
}: {
  source: ImageSearchSource
  result: SourceResultState
  visibleItems: ImageSearchItem[]
  hiddenCount: number
  single: boolean
  selectedKey: string
  query: string
  onSelect: (item: ImageSearchItem) => void
  onRetry: () => void
  onLoadMore: () => void
  onUnavailable: (key: string) => void
}) {
  const isLocal = source.id === 'local'
  return (
    <section className="image-search-section" aria-label={`${source.name} 搜索结果`}>
      <header className="image-search-section__head">
        <div className="image-search-section__title">
          <strong>{source.name}</strong>
          <span className="image-search-section__license">{source.license}</span>
          {source.requires_attribution ? (
            <span className="image-search-section__badge">需署名</span>
          ) : null}
        </div>
        <div className="image-search-section__status">
          {hiddenCount > 0 ? (
            <span className="image-search-section__hidden">已过滤 {hiddenCount} 张</span>
          ) : null}
          <span className="image-search-section__count">
            {result.loading ? '加载中' : visibleItems.length ? `${visibleItems.length} 张` : ''}
          </span>
        </div>
      </header>

      {result.skipped && single ? (
        <div className="image-search-section__hint">
          当前筛选不适用于该图源，请切换类型或图源
        </div>
      ) : null}

      {result.loading ? (
        <div className="image-search-grid" aria-hidden="true">
          {SKELETONS.map((index) => (
            <span key={index} className="image-search-skeleton" />
          ))}
        </div>
      ) : null}

      {!result.loading && result.error ? (
        <div className="image-search-section__error">
          <span>{result.error}</span>
          <Button size="small" onClick={onRetry}>重试</Button>
        </div>
      ) : null}

      {!result.loading && !result.error && visibleItems.length === 0 ? (
        hiddenCount > 0 ? (
          <div className="image-search-section__hint">
            已隐藏 {hiddenCount} 张无法加载的图片
          </div>
        ) : single ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={isLocal && !query.trim()
              ? '还没有本站素材，上传图片后会自动出现在这里'
              : !query.trim()
                ? `输入关键词搜索 ${source.name} 图片`
                : '没有找到相关图片'}
          />
        ) : null
      ) : null}

      {visibleItems.length > 0 ? (
        <div className="image-search-grid">
          {visibleItems.map((item) => (
            <Card
              key={JSON.stringify([item.source, item.source_id, item.thumb_proxy_url, item.preview_proxy_url, item.thumb_url, item.preview_url])}
              item={item}
              selected={selectedKey === itemKey(item)}
              onSelect={() => onSelect(item)}
              onUnavailable={onUnavailable}
            />
          ))}
        </div>
      ) : null}

      {result.hasMore && !result.loading ? (
        <div className="image-search-section__more">
          <Button type="link" size="small" onClick={onLoadMore}>加载更多</Button>
        </div>
      ) : null}
    </section>
  )
}

export default function ImageSearchGrid({
  sources, results, activeSource, selectedKey, query, onSelect, onRetry, onLoadMore,
}: Props) {
  // 失效图片按图源分组记录；换一次搜索就整体重置，让同一张图有机会重新加载。
  const [hidden, setHidden] = useState<Record<string, Set<string>>>({})
  const [hiddenQuery, setHiddenQuery] = useState(query)
  if (hiddenQuery !== query) {
    setHiddenQuery(query)
    setHidden({})
  }

  const markUnavailable = useCallback((sourceId: string, key: string) => {
    setHidden((prev) => {
      const current = prev[sourceId]
      if (current?.has(key)) return prev
      const next = new Set(current)
      next.add(key)
      return { ...prev, [sourceId]: next }
    })
  }, [])

  const single = activeSource !== 'all'
  const visible = single
    ? sources.filter((source) => source.id === activeSource)
    : sources

  const entries = visible.map((source) => {
    const result = results[source.id] || EMPTY_RESULT
    const hiddenSet = hidden[source.id]
    const visibleItems = hiddenSet
      ? result.items.filter((item) => !hiddenSet.has(itemKey(item)))
      : result.items
    return {
      source,
      result,
      visibleItems,
      hiddenCount: result.items.length - visibleItems.length,
    }
  })

  // 「全部」视图只保留有内容的图源：加载中、报错（需要给重试入口）、有图。
  // 空结果的图源直接折叠，不再用一整块空态把有图的图源挤到下面。
  const shown = single
    ? entries
    : entries.filter((entry) => (
      entry.result.loading || Boolean(entry.result.error) || entry.visibleItems.length > 0
    ))
  // 折叠的是「一条结果都没有」的图源；有结果但图片全失效的，只计入过滤张数，
  // 否则同一件事会在文案里被数两遍。
  const foldedSources = single
    ? []
    : entries.filter((entry) => (
      !entry.result.loading && !entry.result.error && !entry.result.skipped
      && entry.result.items.length === 0
    ))
  const filteredImages = entries.reduce((sum, entry) => sum + entry.hiddenCount, 0)
  const failedSources = entries.filter((entry) => !entry.result.loading && entry.result.error)
  const hasQuery = Boolean(query.trim())
  const showNotice = !single && hasQuery && (foldedSources.length > 0 || filteredImages > 0)
  const noticeParts: string[] = []
  if (foldedSources.length > 0) noticeParts.push(`${foldedSources.length} 个无结果图源`)
  if (filteredImages > 0) noticeParts.push(`${filteredImages} 张无法加载的图片`)
  const noticeText = `已自动过滤 ${noticeParts.join('、')}`

  return (
    <div className="image-search-sections">
      {showNotice ? (
        <div className="image-search-notice">
          <span>{noticeText}</span>
          {failedSources.length > 0 ? (
            <span className="image-search-notice__actions">
              {failedSources.map((entry) => (
                <Button
                  key={entry.source.id}
                  type="link"
                  size="small"
                  onClick={() => onRetry(entry.source.id)}
                >
                  重试 {entry.source.name}
                </Button>
              ))}
            </span>
          ) : null}
        </div>
      ) : null}

      {shown.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={hasQuery ? '没有搜到可用的图片' : '输入关键词搜索图片'}
        />
      ) : null}

      {shown.map((entry) => (
        <Section
          key={`${entry.source.id}:${query}`}
          source={entry.source}
          result={entry.result}
          visibleItems={entry.visibleItems}
          hiddenCount={entry.hiddenCount}
          single={single}
          selectedKey={selectedKey}
          query={query}
          onSelect={onSelect}
          onRetry={() => onRetry(entry.source.id)}
          onLoadMore={() => onLoadMore(entry.source.id)}
          onUnavailable={(key) => markUnavailable(entry.source.id, key)}
        />
      ))}
    </div>
  )
}
