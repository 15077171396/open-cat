/**
 * 新增页面（登录日志 / 错误日志 / 敏感词库 Tab）的「能渲染」冒烟测试。
 *
 * 与 SettingsPages.smoke 同一理由：这些页面都是 lazy() 加载的，渲染期一抛异常，
 * 用户看到的就是错误边界 / 白屏。断言刻意只用「整页文本包含」，
 * 避免同一文案出现在页头与表格里时 getByText 全等匹配报 multiple elements。
 */

import { render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(async () => ({ data: {} })), post: vi.fn(async () => ({})) },
  apiGet: vi.fn(async () => ({})),
  apiPost: vi.fn(async () => ({})),
  apiPut: vi.fn(async () => ({})),
  apiDelete: vi.fn(async () => ({})),
}))

import { apiGet } from '../api/client'
import { useAuthStore } from '../store/authStore'
import LoginLogsList from './LoginLogs/List'
import MonitorErrors from './Monitor/Errors'
import WordsTab from './Moderation/WordsTab'
import IpBlacklistPage from './IpBlacklist'
import MonitorApiStats from './Monitor/ApiStats'

const getRaw = apiGet as unknown as ReturnType<typeof vi.fn>

/** 渲染并等待整页文本出现某段内容 */
async function expectText(node: React.ReactNode, text: string) {
  const { container } = render(<MemoryRouter>{node}</MemoryRouter>)
  await waitFor(() => expect(container.textContent || '').toContain(text))
}

describe('新增页面渲染冒烟', () => {
  beforeEach(() => {
    getRaw.mockReset()
    // 冒烟测试要验的是「点得动」，统一当作超管（PermGuard 全放行）
    useAuthStore.setState({ isSuper: true, permissions: null })
  })

  it('登录日志：页头 + 空表渲染（筛选按脱敏精确匹配的提示在场）', async () => {
    getRaw.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
    await expectText(<LoginLogsList />, '登录日志')
    await waitFor(() => expect(document.body.textContent || '').toContain('暂无登录记录'))
  })

  it('登录日志：渲染一条失败记录并显示状态标签', async () => {
    getRaw.mockResolvedValue({
      items: [
        {
          id: 1,
          user_id: null,
          account: '138****5678',
          status: 'failed',
          message: '账号或密码错误',
          ip: '1.2.3.4',
          user_agent: 'UA',
          request_id: null,
          created_at: '2026-11-18T01:02:03',
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })
    await expectText(<LoginLogsList />, '138****5678')
    await waitFor(() => expect(document.body.textContent || '').toContain('登录失败'))
  })

  it('错误日志：缓冲为空时显示「暂无错误事件」提示', async () => {
    getRaw.mockResolvedValue({
      available: false,
      error: '暂无错误事件（或 Redis 不可用无法读取）',
      items: [],
    })
    await expectText(<MonitorErrors />, '错误日志')
    await waitFor(() => expect(document.body.textContent || '').toContain('暂无错误事件'))
  })

  it('错误日志：渲染一条 CRITICAL 事件', async () => {
    getRaw.mockResolvedValue({
      available: true,
      error: null,
      items: [
        {
          ts: '2026-11-18T01:02:03',
          level: 'CRITICAL',
          message: '数据库连接失败',
          name: 'app.main',
          function: 'lifespan',
          line: 42,
          request_id: 'req-9',
        },
      ],
    })
    await expectText(<MonitorErrors />, '数据库连接失败')
    await waitFor(() => expect(document.body.textContent || '').toContain('CRITICAL'))
  })

  it('敏感词库 Tab：内置词与自定义词分组展示', async () => {
    getRaw.mockResolvedValue({
      builtin_words: ['加微信', '刷单'],
      extra_words: ['站点专属词'],
      max_words: 500,
      word_max_len: 40,
    })
    await expectText(<WordsTab active />, '自定义词库')
    await waitFor(() => expect(document.body.textContent || '').toContain('站点专属词'))
    await waitFor(() => expect(document.body.textContent || '').toContain('加微信'))
  })

  it('IP 黑名单：页头与条目计数渲染', async () => {
    getRaw.mockResolvedValue({ entries: [], invalid_entries: [], max_entries: 1000 })
    await expectText(<IpBlacklistPage />, 'IP 黑名单')
    await waitFor(() => expect(document.body.textContent || '').toContain('CIDR'))
  })

  it('IP 黑名单：渲染既有条目并提示无效行', async () => {
    getRaw.mockResolvedValue({
      entries: ['203.0.113.7', '10.0.0.0/8'],
      invalid_entries: ['bad-entry'],
      max_entries: 1000,
    })
    await expectText(<IpBlacklistPage />, '203.0.113.7')
    await waitFor(() => expect(document.body.textContent || '').toContain('1 行格式无效'))
  })

  it('接口监控：尚未采集时的降级提示', async () => {
    getRaw.mockResolvedValue({
      available: false,
      enabled: false,
      slow_threshold_ms: 1000,
      min_samples: 5,
      unique_paths: 0,
      total_calls: 0,
      top_calls: [],
      top_errors: [],
      slow_requests: [],
    })
    await expectText(<MonitorApiStats />, '接口监控')
    await waitFor(() => expect(document.body.textContent || '').toContain('尚未采集'))
  })

  it('接口监控：调用量 Top 与慢接口明细渲染', async () => {
    getRaw.mockResolvedValue({
      available: true,
      enabled: true,
      slow_threshold_ms: 1000,
      min_samples: 5,
      unique_paths: 2,
      total_calls: 120,
      top_calls: [{ key: 'GET /api/v1/admin/users', calls: 100, '4xx': 2, '5xx': 1, error_rate: 0.03 }],
      top_errors: [],
      slow_requests: [
        { ts: '2026-11-19T01:02:03', method: 'POST', path: '/api/v1/admin/export/users', status: 200, duration_ms: 3200, user_id: 'authed' },
      ],
    })
    await expectText(<MonitorApiStats />, 'GET /api/v1/admin/users')
    await waitFor(() => expect(document.body.textContent || '').toContain('3.2s'))
  })
})
