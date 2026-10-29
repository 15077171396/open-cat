import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ImageSearchModal from './ImageSearchModal'
import { importImage } from './imageSearchApi'
import type { ImageSearchItem, ImageSearchOptions, SourceResultState } from './types'
import { useImageSearch } from './useImageSearch'

vi.mock('./useImageSearch', () => ({ useImageSearch: vi.fn() }))
vi.mock('./imageSearchApi', () => ({ importImage: vi.fn(), touchMaterial: vi.fn().mockResolvedValue(undefined) }))

const options: ImageSearchOptions = {
  enabled: true, page_size: 12, cos_configured: true, local_available: true,
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
  ],
}

const item: ImageSearchItem = {
  source: 'pexels', source_id: 'p1', thumb_url: 'https://images.pexels.com/t.jpg',
  preview_url: 'https://images.pexels.com/l.jpg',
  thumb_proxy_url: '/thumb?token=1', preview_proxy_url: '/preview?token=1',
  width: 1200, height: 800, alt: '一只猫', title: '猫', author: 'Alice',
  author_url: 'https://www.pexels.com/@alice',
  page_url: 'https://www.pexels.com/photo/1/',
  license: 'Pexels License', license_url: 'https://www.pexels.com/license/',
  requires_attribution: false,
}

const empty: SourceResultState = {
  items: [], page: 1, hasMore: false, loading: false, error: '', skipped: false,
}

function hook(overrides: Record<string, unknown> = {}) {
  return {
    options,
    optionsLoading: false,
    optionsError: '',
    reloadOptions: vi.fn(),
    sources: options.sources.filter((source) => source.configured),
    query: 'cat',
    setQuery: vi.fn(),
    activeSource: 'all',
    setActiveSource: vi.fn(),
    results: {
      pexels: { ...empty, items: [item] },
      pixabay: { ...empty },
    },
    search: vi.fn().mockResolvedValue(undefined),
    loadMore: vi.fn().mockResolvedValue(undefined),
    retry: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

function renderModal(params: { hookValue?: ReturnType<typeof hook>; onInsert?: () => void } = {}) {
  const hookValue = params.hookValue || hook()
  vi.mocked(useImageSearch).mockReturnValue(hookValue as never)
  const onInsert = params.onInsert || vi.fn()
  const onClose = vi.fn()
  render(
    <ImageSearchModal
      open
      initialQuery="cat"
      onClose={onClose}
      onInsert={onInsert}
    />,
  )
  return { hookValue, onInsert, onClose }
}

describe('ImageSearchModal', () => {
  beforeEach(() => {
    vi.mocked(importImage).mockReset()
    vi.mocked(useImageSearch).mockReset()
  })

  it('does not search while typing, only on enter or the search button', async () => {
    const user = userEvent.setup()
    const { hookValue } = renderModal()
    const input = screen.getByLabelText('搜索图片关键词')

    // 打开弹窗时会按 initialQuery 自动搜一次，这是基线
    const baseline = hookValue.search.mock.calls.length

    await user.type(input, '熊猫')
    // 超过旧实现里 400ms 防抖的窗口：如果还在「边打字边搜」，这里必然多出调用
    await new Promise((resolve) => { setTimeout(resolve, 500) })
    expect(hookValue.search.mock.calls.length).toBe(baseline)

    await user.keyboard('{Enter}')
    expect(hookValue.search).toHaveBeenLastCalledWith('cat熊猫', expect.anything())

    await user.click(screen.getByRole('button', { name: /搜\s*索/ }))
    expect(hookValue.search).toHaveBeenLastCalledWith('cat熊猫', expect.anything())
  })

  it('shows disabled guidance when feature is off', () => {
    renderModal({
      hookValue: hook({ options: { ...options, enabled: false }, sources: [] }),
    })
    expect(screen.getByText('图片搜索未开启')).toBeInTheDocument()
  })

  it('renders sources and lets the author preview and insert', async () => {
    const user = userEvent.setup()
    const credit = {
      text: '图片：Alice / Pexels', url: 'https://www.pexels.com/photo/1/',
      license: 'Pexels License', license_url: '', requires_attribution: false,
    }
    vi.mocked(importImage).mockResolvedValue({
      url: 'https://cos.example/stock.webp', thumb_url: null, width: 1200,
      height: 800, alt: '一只猫', asset_id: 1, deduped: false, credit,
    })
    const { onInsert, onClose } = renderModal()

    await user.click(screen.getByRole('button', { name: '一只猫' }))
    expect(screen.getByLabelText('图片说明')).toHaveValue('一只猫')

    await user.click(screen.getByRole('button', { name: '插入正文' }))
    await waitFor(() => expect(onInsert).toHaveBeenCalledWith({
      url: 'https://cos.example/stock.webp',
      alt: '一只猫',
      credit,
    }))
    expect(onClose).toHaveBeenCalled()
  })

  it('disables insertion when alt is empty', async () => {
    const user = userEvent.setup()
    renderModal({
      hookValue: hook({
        results: {
          pexels: { ...empty, items: [{ ...item, alt: '', title: '' }] },
          pixabay: { ...empty },
        },
      }),
    })
    await user.click(screen.getByRole('button', { name: '选择图片' }))
    expect(screen.getByLabelText('图片说明')).toHaveValue('')
    expect(screen.getByRole('button', { name: '插入正文' })).toBeDisabled()
  })

  it('does not add a credit line for local assets by default', async () => {
    const user = userEvent.setup()
    const local = { ...item, source: 'local', source_id: '9', author: '' }
    renderModal({
      hookValue: hook({
        sources: [
          {
            id: 'local', name: '本站素材', configured: true, license: '本站上传',
            license_url: '', requires_attribution: false,
            types: ['photo', 'illustration'], help_url: '',
          },
          ...options.sources.filter((source) => source.configured),
        ],
        results: {
          pexels: empty,
          pixabay: empty,
          local: { ...empty, items: [local] },
        },
      }),
    })
    await user.click(screen.getByRole('button', { name: '一只猫' }))
    expect(screen.getByRole('checkbox', { name: /插入署名/ })).not.toBeChecked()
  })

  it('locks attribution for sources that require it', async () => {
    const user = userEvent.setup()
    const required = { ...item, source: 'pixabay', source_id: 'b1', requires_attribution: true }
    renderModal({
      hookValue: hook({
        results: {
          pexels: { ...empty },
          pixabay: { ...empty, items: [required] },
        },
      }),
    })
    await user.click(screen.getByRole('button', { name: '一只猫' }))
    const checkbox = screen.getByRole('checkbox', { name: /插入署名/ })
    expect(checkbox).toBeChecked()
    expect(checkbox).toBeDisabled()
  })

  it('shows a per-source error without hiding other sources', () => {
    renderModal({
      hookValue: hook({
        results: {
          pexels: { ...empty, items: [item] },
          pixabay: { ...empty, error: 'Pixabay 请求超时' },
        },
      }),
    })
    expect(screen.getByText('Pixabay 请求超时')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '一只猫' })).toBeInTheDocument()
  })

  it('disables insertion when COS is not configured', async () => {
    const user = userEvent.setup()
    renderModal({
      hookValue: hook({ options: { ...options, cos_configured: false } }),
    })
    await user.click(screen.getByRole('button', { name: '一只猫' }))
    expect(screen.getByText('COS 未配置，暂时无法保存图片')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '插入正文' })).toBeDisabled()
  })

  it('shows import errors inline', async () => {
    const user = userEvent.setup()
    vi.mocked(importImage).mockRejectedValue(new Error('图片存储失败'))
    renderModal()
    await user.click(screen.getByRole('button', { name: '一只猫' }))
    await user.click(screen.getByRole('button', { name: '插入正文' }))
    await waitFor(() => expect(screen.getByText('图片存储失败')).toBeInTheDocument())
  })
})
