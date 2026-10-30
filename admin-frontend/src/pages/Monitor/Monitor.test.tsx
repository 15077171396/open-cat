import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  const post = vi.fn()
  return { apiGet: get, apiPost: post }
})

import { apiGet, apiPost } from '../../api/client'
import { useAuthStore } from '../../store/authStore'
import MonitorOnline from './Online'
import MonitorServer from './Server'
import MonitorCache from './Cache'

const mockedGet = vi.mocked(apiGet)
const mockedPost = vi.mocked(apiPost)

const SERVER = {
  available: true,
  error: null,
  platform: 'Linux 6.1',
  python_version: '3.13.0',
  cpu_count: 8,
  cpu_percent: 12.5,
  load_avg: [0.2, 0.3, 0.4],
  memory_total: 8 * 1024 ** 3,
  memory_used: 4 * 1024 ** 3,
  memory_percent: 50,
  swap_total: 1024 ** 3,
  swap_used: 0,
  swap_percent: 0,
  disk_total: 100 * 1024 ** 3,
  disk_used: 40 * 1024 ** 3,
  disk_percent: 40,
  boot_time: 1,
  uptime_seconds: 90061,
  process_pid: 123,
  process_memory: 64 * 1024 ** 2,
  process_threads: 12,
}

const CACHE = {
  available: true,
  error: null,
  version: '7.2.0',
  used_memory: 1024,
  used_memory_human: '1.0M',
  peak_memory_human: '2.0M',
  maxmemory_human: '未限制',
  connected_clients: 3,
  total_keys: 42,
  hits: 90,
  misses: 10,
  hit_rate: 90,
  uptime_seconds: 3600,
  scanned: 42,
  truncated: false,
  prefixes: [{ prefix: 'sess:', count: 20 }],
}

const ONLINE = {
  available: true,
  items: [
    {
      sid: 'sid-a',
      uid: 7,
      account: 'alice@test',
      nickname: 'Alice',
      role: 'user',
      device: 'Chrome · Windows',
      ip: '1.1.1.1',
      created_at: 100,
      last_seen: 200,
    },
  ],
  total: 1,
  unique_users: 1,
  page: 1,
  page_size: 20,
  scanned: 1,
}

describe('监控管理页面', () => {
  beforeEach(() => {
    useAuthStore.setState({ isSuper: true, permissions: [] })
    mockedGet.mockReset()
    mockedPost.mockReset()
    mockedPost.mockResolvedValue({ ok: true })
    mockedGet.mockImplementation((url: string) => {
      if (url === '/admin/monitor/server') return Promise.resolve(SERVER)
      if (url === '/admin/monitor/cache') return Promise.resolve(CACHE)
      if (url === '/admin/monitor/online') return Promise.resolve(ONLINE)
      return Promise.resolve({})
    })
  })

  afterEach(() => {
    useAuthStore.setState({ isSuper: false, permissions: null })
  })

  it('服务器监控展示 CPU / 内存 / 磁盘', async () => {
    render(<MonitorServer />)
    expect(await screen.findByText('CPU 使用率')).toBeInTheDocument()
    expect(screen.getByText('内存使用率')).toBeInTheDocument()
    expect(screen.getByText('磁盘使用率')).toBeInTheDocument()
    expect(screen.getByText('Linux 6.1')).toBeInTheDocument()
  })

  it('缓存监控展示键数、命中率与键前缀', async () => {
    render(<MonitorCache />)
    expect(await screen.findByText('键总数')).toBeInTheDocument()
    expect(screen.getByText('命中率')).toBeInTheDocument()
    expect(screen.getByText('sess:')).toBeInTheDocument()
  })

  it('在线用户展示账号设备，并支持强制下线', async () => {
    render(<MonitorOnline />)
    expect(await screen.findByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('Chrome · Windows')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /强制下线/ }))
    await userEvent.click(await screen.findByRole('button', { name: /OK|确\s*定/ }))
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/admin/users/7/force-logout')
    })
  })
})
