import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ImageSearchGrid from './ImageSearchGrid'
import ImageSearchPreview from './ImageSearchPreview'
import type { ImageSearchItem, ImageSearchSource } from './types'

const source: ImageSearchSource = {
  id: 'sogou', name: '搜狗图片', configured: true, license: '网络图片',
  license_url: '', requires_attribution: false, types: ['photo'], help_url: '',
}

const item: ImageSearchItem = {
  source: 'sogou', source_id: '1',
  thumb_url: 'https://example.com/thumb.jpg',
  thumb_proxy_url: '/api/v1/images/search-thumb?token=thumbnail',
  preview_url: 'https://example.com/preview.jpg',
  preview_proxy_url: '/api/v1/images/search-thumb?token=preview',
  width: 100, height: 80, alt: '猫', title: '猫', author: '',
  author_url: '', page_url: '', license: '', license_url: '',
  requires_attribution: false,
}

function grid(items = [item], query = '猫', activeSource = 'all') {
  return (
    <ImageSearchGrid
      sources={[source]}
      results={{ sogou: { items, page: 1, hasMore: true, loading: false, error: '', skipped: false } }}
      activeSource={activeSource} selectedKey="" query={query}
      onSelect={vi.fn()} onRetry={vi.fn()} onLoadMore={vi.fn()}
    />
  )
}

function loaded(image: HTMLElement) {
  Object.defineProperties(image, {
    naturalWidth: { configurable: true, value: 100 },
    complete: { configurable: true, value: true },
  })
  fireEvent.load(image)
}

afterEach(() => {
  vi.useRealTimers()
})

describe('ImageSearchGrid', () => {
  it('removes failed external images without bypassing the checked proxy', () => {
    render(grid())
    const image = screen.getByRole('img')
    fireEvent.error(image)
    expect(image).toHaveAttribute('src', item.preview_proxy_url)
    fireEvent.error(image)
    expect(screen.queryByRole('button', { name: '猫' })).not.toBeInTheDocument()
    expect(screen.getByText('已自动过滤 1 张无法加载的图片')).toBeInTheDocument()
  })

  it('folds a source that returned nothing instead of leaving a blank block', () => {
    render(grid([], '熊猫'))
    expect(screen.getByText('已自动过滤 1 个无结果图源')).toBeInTheDocument()
    expect(screen.queryByText('没有找到相关图片')).not.toBeInTheDocument()
  })

  it('still explains an empty result when only that source is selected', () => {
    render(grid([], '熊猫', 'sogou'))
    expect(screen.getByText('没有找到相关图片')).toBeInTheDocument()
  })

  it('does not count a source twice when every image in it failed', () => {
    render(grid([], '熊猫'))
    expect(screen.getByText('已自动过滤 1 个无结果图源')).toBeInTheDocument()
    expect(screen.queryByText(/张无法加载的图片/)).not.toBeInTheDocument()
  })

  it('hides an image that never finishes loading', async () => {
    vi.useFakeTimers()
    render(grid())
    // 两个候选地址都不会有 load/error 回调（防盗链或超时），逐个计时兜底。
    Object.defineProperty(screen.getByRole('img'), 'complete', {
      configurable: true, value: false,
    })
    // 分两轮推进：React 在 await 点后才 flush 重渲染，第二轮计时器
    // 是在第一轮超时之后才建立的，一次性推到底会错过它。
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.queryByRole('button', { name: '猫' })).not.toBeInTheDocument()
    expect(screen.getByText('已自动过滤 1 张无法加载的图片')).toBeInTheDocument()
  })

  it('tries each distinct candidate only once', () => {
    render(grid([{ ...item, preview_proxy_url: item.thumb_proxy_url }]))
    fireEvent.error(screen.getByRole('img'))
    expect(screen.queryByRole('button', { name: '猫' })).not.toBeInTheDocument()
  })

  it('shows a successful fallback and hides broken pixels while loading', () => {
    render(grid())
    const image = screen.getByRole('img')
    expect(image).toHaveClass('is-loading')
    fireEvent.error(image)
    expect(image).toHaveClass('is-loading')
    loaded(screen.getByRole('img'))
    expect(screen.getByRole('img')).toHaveClass('is-loaded')
    expect(screen.getByRole('button', { name: '猫' })).not.toBeDisabled()
  })

  it('does not show a previously loaded image when its fallback is pending', () => {
    render(grid())
    loaded(screen.getByRole('img'))
    fireEvent.error(screen.getByRole('img'))
    expect(screen.getByRole('img')).toHaveClass('is-loading')
  })

  it('does not resurrect failed cards when loading another page', () => {
    const { rerender } = render(grid())
    fireEvent.error(screen.getByRole('img'))
    fireEvent.error(screen.getByRole('img'))
    const next = { ...item, source_id: '2', alt: '另一张猫' }
    rerender(grid([item, next]))
    expect(screen.queryByRole('button', { name: '猫' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '另一张猫' })).toBeInTheDocument()
    expect(screen.getByText('1 张')).toBeInTheDocument()
  })

  it('can try the same card again in a new search', () => {
    const { rerender } = render(grid())
    fireEvent.error(screen.getByRole('img'))
    fireEvent.error(screen.getByRole('img'))
    rerender(grid([item], '熊猫'))
    expect(screen.getByRole('img')).toHaveAttribute('src', item.thumb_proxy_url)
  })

  it('recognizes an already decoded cached image', async () => {
    vi.useFakeTimers()
    render(grid())
    Object.defineProperties(screen.getByRole('img'), {
      complete: { configurable: true, value: true },
      naturalWidth: { configurable: true, value: 100 },
    })
    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    expect(screen.getByRole('img')).toHaveClass('is-loaded')
  })

  it('keeps preview fallback behind the same placeholder check', () => {
    render(<ImageSearchPreview
      item={item} sourceName="搜狗图片" alt="猫" attribution={false} importing={false} importError=""
      cosConfigured onAltChange={vi.fn()} onAttributionChange={vi.fn()} onInsert={vi.fn()}
    />)
    fireEvent.error(screen.getByRole('img'))
    expect(screen.getByRole('img')).toHaveAttribute('src', item.thumb_proxy_url)
    fireEvent.error(screen.getByRole('img'))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('keeps local COS images usable without proxy URLs', () => {
    render(grid([{
      ...item, source: 'local', thumb_proxy_url: '', preview_proxy_url: '',
    }]))
    expect(screen.getByRole('img')).toHaveAttribute('src', item.thumb_url)
    loaded(screen.getByRole('img'))
    expect(screen.getByRole('button', { name: '猫' })).not.toBeDisabled()
  })
})
