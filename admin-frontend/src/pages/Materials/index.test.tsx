import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { copyText } from '../../utils/clipboard'
import MaterialsPage from './index'
import { fetchMaterials, fetchMaterialStats, touchMaterial } from './api'

vi.mock('./api', () => ({
  fetchMaterials: vi.fn(),
  fetchMaterialStats: vi.fn(),
  touchMaterial: vi.fn(),
}))
vi.mock('../../utils/clipboard', () => ({ copyText: vi.fn() }))

const items = [
  {
    id: 1, kind: 'image' as const, source: 'upload', title: '猫咪配图', filename: 'cat.png',
    alt: '一只猫', author: '', mime_type: 'image/png', width: 320, height: 200,
    size_bytes: 2048, cos_url: 'https://cos.example/cat.png',
    thumb_url: 'https://cos.example/cat.png', license: '本站上传', page_url: '',
    usage_count: 2, last_used_at: '', created_at: '2026-10-29T10:00:00',
  },
  {
    id: 2, kind: 'document' as const, source: 'upload', title: '使用手册', filename: 'manual.pdf',
    alt: '', author: '', mime_type: 'application/pdf', width: 0, height: 0,
    size_bytes: 4096, cos_url: 'https://cos.example/manual.pdf',
    thumb_url: '', license: '本站上传', page_url: '',
    usage_count: 0, last_used_at: '', created_at: '2026-10-29T10:00:00',
  },
]

describe('MaterialsPage', () => {
  beforeEach(() => {
    vi.mocked(fetchMaterials).mockReset()
    vi.mocked(fetchMaterialStats).mockReset()
    vi.mocked(touchMaterial).mockReset()
    vi.mocked(copyText).mockReset()
    vi.mocked(fetchMaterials).mockResolvedValue({
      items, total: 2, page: 1, page_size: 24,
    })
    vi.mocked(fetchMaterialStats).mockResolvedValue({
      total: 2, kinds: { image: 1, document: 1 },
    })
    vi.mocked(touchMaterial).mockResolvedValue(items[0])
    vi.mocked(copyText).mockResolvedValue(true)
  })

  it('renders image and file materials', async () => {
    render(<MaterialsPage />)
    expect(await screen.findByText('猫咪配图')).toBeInTheDocument()
    expect(screen.getByText('使用手册')).toBeInTheDocument()
    expect(screen.getByText('全部素材')).toBeInTheDocument()
    expect(screen.getByText('使用 2 次')).toBeInTheDocument()
  })

  it('filters by kind and keyword', async () => {
    const user = userEvent.setup()
    render(<MaterialsPage />)
    await screen.findByText('猫咪配图')

    await user.click(screen.getByRole('button', { name: /图片/ }))
    await waitFor(() => expect(fetchMaterials).toHaveBeenLastCalledWith(
      expect.objectContaining({ kind: 'image', page: 1 }),
    ))

    await user.type(screen.getByLabelText('搜索素材'), '猫')
    await user.click(screen.getByRole('button', { name: /搜\s*索/ }))
    await waitFor(() => expect(fetchMaterials).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '猫', kind: 'image', page: 1 }),
    ))
  })

  it('copies the asset address and records usage', async () => {
    const user = userEvent.setup()
    render(<MaterialsPage />)
    await screen.findByText('猫咪配图')

    await user.click(screen.getAllByRole('button', { name: '复制地址' })[0])
    await waitFor(() => expect(copyText).toHaveBeenCalledWith('https://cos.example/cat.png'))
    await waitFor(() => expect(touchMaterial).toHaveBeenCalledWith(1))
  })

  it('opens a preview', async () => {
    const user = userEvent.setup()
    render(<MaterialsPage />)
    await screen.findByText('猫咪配图')

    await user.click(screen.getByRole('button', { name: '预览 猫咪配图' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getAllByText('复制 Markdown').length).toBeGreaterThan(0)
  })
})
