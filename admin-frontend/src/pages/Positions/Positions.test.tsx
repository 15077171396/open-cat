import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  const post = vi.fn()
  const put = vi.fn()
  const del = vi.fn()
  return { apiGet: get, apiPost: post, apiPut: put, apiDelete: del }
})

import { apiDelete, apiGet, apiPost, apiPut } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import Positions from './index'

const mockedGet = vi.mocked(apiGet)
const mockedPost = vi.mocked(apiPost)
const mockedPut = vi.mocked(apiPut)
const mockedDelete = vi.mocked(apiDelete)

const ROWS = {
  items: [
    {
      id: 1,
      code: 'content_operator',
      name: '内容运营',
      description: '负责内容更新',
      sort_order: 1,
      is_enabled: true,
    },
  ],
}

describe('Positions', () => {
  beforeEach(() => {
    useAuthStore.setState({ isSuper: true, permissions: [] })
    mockedGet.mockReset()
    mockedPost.mockReset()
    mockedPut.mockReset()
    mockedDelete.mockReset()
    mockedGet.mockResolvedValue(ROWS)
    mockedPost.mockResolvedValue({ id: 2 })
    mockedPut.mockResolvedValue({ ok: true })
    mockedDelete.mockResolvedValue({ ok: true })
  })

  afterEach(() => {
    useAuthStore.setState({ isSuper: false, permissions: null })
  })

  it('渲染岗位列表', async () => {
    render(<Positions />)
    expect(await screen.findByText('内容运营')).toBeInTheDocument()
    expect(screen.getByText('content_operator')).toBeInTheDocument()
    expect(screen.getByText('负责内容更新')).toBeInTheDocument()
  })

  it('切换启用状态会调用接口', async () => {
    render(<Positions />)
    await screen.findByText('内容运营')
    await userEvent.click(screen.getByRole('switch', { name: '内容运营 是否启用' }))
    await waitFor(() => {
      expect(mockedPut).toHaveBeenCalledWith('/admin/positions/1', { is_enabled: false })
    })
  })
})
