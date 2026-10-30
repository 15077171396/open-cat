import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  const download = vi.fn()
  return { apiGet: get, apiDownload: download, apiPost: vi.fn(), apiPut: vi.fn(), apiDelete: vi.fn() }
})

import { apiDownload, apiGet } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import Codegen from './index'

const mockedGet = vi.mocked(apiGet)
const mockedDownload = vi.mocked(apiDownload)

const TABLES = {
  available: true,
  database: 'open_cat',
  error: '',
  items: [
    {
      name: 'positions',
      comment: '岗位表',
      columns: [
        { name: 'id', type: 'BIGINT', nullable: false, comment: '', primary: true, editable: false },
        { name: 'code', type: 'VARCHAR(50)', nullable: false, comment: '岗位编码', primary: false, editable: true },
        { name: 'name', type: 'VARCHAR(50)', nullable: false, comment: '岗位名称', primary: false, editable: true },
      ],
      suggested: {
        module: 'sample_positions',
        class_name: 'SamplePositions',
        title: '岗位表',
        route_prefix: '/sample-positions',
        fields: ['code', 'name'],
      },
    },
  ],
}

const PREVIEW = {
  available: true,
  warnings: ['目标文件已存在：backend/app/models/sample_positions.py'],
  files: [
    {
      path: 'backend/app/models/sample_positions.py',
      content: 'class SamplePositions(Base):\n    pass\n',
    },
    {
      path: 'INTEGRATION.md',
      content: '## 接入步骤\n',
    },
  ],
}

describe('Codegen', () => {
  beforeEach(() => {
    useAuthStore.setState({ isSuper: true, permissions: [] })
    mockedGet.mockReset()
    mockedDownload.mockReset()
    mockedGet.mockImplementation((url: string) => {
      if (url === '/admin/codegen/tables') return Promise.resolve(TABLES)
      if (url === '/admin/codegen/preview') return Promise.resolve(PREVIEW)
      return Promise.resolve({})
    })
    mockedDownload.mockResolvedValue(new Blob(['PK'], { type: 'application/zip' }))
  })

  afterEach(() => {
    useAuthStore.setState({ isSuper: false, permissions: null })
  })

  it('加载表清单并生成预览', async () => {
    render(<Codegen />)
    expect(await screen.findByText('positions')).toBeInTheDocument()
    expect(screen.getByText('岗位表 · 3 列')).toBeInTheDocument()
    expect(screen.getByDisplayValue('sample_positions')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /生成预览/ }))

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/admin/codegen/preview', {
        params: {
          table: 'positions',
          module: 'sample_positions',
          class_name: 'SamplePositions',
          title: '岗位表',
          route_prefix: '/sample-positions',
          fields: 'code,name',
        },
      })
    })
    expect(await screen.findByText(/class SamplePositions/)).toBeInTheDocument()
    expect(screen.getByText(/目标文件已存在/)).toBeInTheDocument()
    expect(screen.getByText('backend/app/models/sample_positions.py')).toBeInTheDocument()
  })

  it('下载 ZIP 走二进制接口并触发浏览器下载', async () => {
    const createObjectURL = vi.fn(() => 'blob:test')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: createObjectURL,
      configurable: true,
    })
    Object.defineProperty(window.URL, 'revokeObjectURL', {
      value: revokeObjectURL,
      configurable: true,
    })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(<Codegen />)
    await screen.findByText('positions')
    await userEvent.click(screen.getByRole('button', { name: /生成预览/ }))
    await screen.findByText(/class SamplePositions/)
    await userEvent.click(screen.getByRole('button', { name: /下载 ZIP/ }))

    await waitFor(() => {
      expect(mockedDownload).toHaveBeenCalledWith(
        '/admin/codegen/download',
        expect.objectContaining({ table: 'positions', module: 'sample_positions' }),
      )
    })
    expect(createObjectURL).toHaveBeenCalled()
    expect(click).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalled()
    click.mockRestore()
  })

  it('没有保存设置权限时下载按钮禁用', async () => {
    useAuthStore.setState({ isSuper: false, permissions: ['setting:view'] })
    render(<Codegen />)
    await screen.findByText('positions')
    await userEvent.click(screen.getByRole('button', { name: /生成预览/ }))
    await screen.findByText(/class SamplePositions/)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /下载 ZIP/ })).toBeDisabled()
    })
  })
})
