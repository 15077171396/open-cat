/**
 * 部署体检页的行为契约。
 *
 * 这一页的价值全在「结论对不对」，所以钉四件事：
 * 1. 有必填项未就绪 → 必须明确说「还有 N 项」并点名，不能显示成绿色「可以上线」；
 * 2. 全部就绪 → 给出可上线结论，且不把可选项的「未启用」算成问题；
 * 3. 「去配置」能带着人跳到对应设置页（否则这页只是张报表，不是向导）；
 * 4. 接口失败 → 给出可操作提示，而不是空白或一直转圈。
 *
 * 状态判定全在后端，前端不做二次推断 —— 所以这里只验呈现，不重复验规则。
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => {
  const get = vi.fn()
  return {
    default: { get, post: vi.fn(async () => ({})) },
    apiGet: get,
    apiPost: vi.fn(async () => ({})),
  }
})

import { apiGet } from '../../api/client'
import Readiness from './index'

const mockedGet = vi.mocked(apiGet)

const READY_DATA = {
  ts: 1,
  storage_driver: 'local',
  app_env: 'production',
  items: [
    {
      key: 'storage',
      label: '文件存储',
      status: 'ready' as const,
      required: true,
      detail: 'local 驱动，写入 /app/uploads（卷已挂载且可写）',
      action: '',
      link: '/settings',
    },
    {
      key: 'mail',
      label: '邮件 SMTP',
      status: 'ready' as const,
      required: true,
      detail: 'smtp.example.com:465 · 发件人 noreply@example.com',
      action: '',
      link: '/mail-settings',
    },
    {
      key: 'ai',
      label: 'AI 能力',
      status: 'off' as const,
      required: false,
      detail: '还没有配置任何模型',
      action: '在「模型管理」里加一个 Provider',
      link: '/ai-models',
    },
  ],
  summary: {
    total: 3,
    ready: 2,
    off: 1,
    error: 0,
    blocking: 0,
    blocking_keys: [],
    deployable: true,
  },
}

const BLOCKED_DATA = {
  ...READY_DATA,
  items: [
    {
      key: 'storage',
      label: '文件存储',
      status: 'ready' as const,
      required: true,
      detail: 'local 驱动',
      action: '',
      link: '/settings',
    },
    {
      key: 'site_base_url',
      label: '站点地址',
      status: 'unconfigured' as const,
      required: true,
      detail: '当前是 http://localhost（本机地址）',
      action: '上线前改成真实域名',
      link: '',
    },
    {
      key: 'mail',
      label: '邮件 SMTP',
      status: 'unconfigured' as const,
      required: true,
      detail: '缺：SMTP_HOST、SMTP_USERNAME',
      action: '未配齐时发不出验证码',
      link: '/mail-settings',
    },
    {
      key: 'payment',
      label: '支付渠道',
      status: 'error' as const,
      required: false,
      detail: '微信支付缺 wechat_pay_mch_id',
      action: '凭据不齐时回调验签不过',
      link: '/payment-settings',
    },
  ],
  summary: {
    total: 4,
    ready: 1,
    off: 0,
    error: 1,
    blocking: 2,
    blocking_keys: ['site_base_url', 'mail'],
    deployable: false,
  },
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/readiness']}>
      <Readiness />
    </MemoryRouter>,
  )
}

describe('部署体检', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('必填项未就绪时点名阻塞项，而不是报「可以上线」', async () => {
    mockedGet.mockResolvedValue(BLOCKED_DATA)
    renderPage()

    await waitFor(() => expect(screen.getByText(/还有 2 项必填未就绪/)).toBeInTheDocument())
    // 点名具体哪两项：只说「2 项」等于让用户自己去猜
    expect(screen.getByText(/站点地址、邮件 SMTP/)).toBeInTheDocument()
    expect(screen.queryByText(/可以上线/)).not.toBeInTheDocument()
  })

  it('「配了但不对」显示为有问题，与「未配置」区分开', async () => {
    mockedGet.mockResolvedValue(BLOCKED_DATA)
    renderPage()

    await waitFor(() => expect(screen.getByText('支付渠道')).toBeInTheDocument())
    // error 用「有问题」，unconfigured 用「未配置」—— 用户能据此判断是自己没填还是填错了
    expect(screen.getByText('有问题')).toBeInTheDocument()
    expect(screen.getAllByText('未配置').length).toBe(2)
  })

  it('全部必填就绪时给出可上线结论，可选项未启用不算问题', async () => {
    mockedGet.mockResolvedValue(READY_DATA)
    renderPage()

    await waitFor(() => expect(screen.getByText(/必填项全部就绪/)).toBeInTheDocument())
    // AI 是 off：必须显示「未启用」而不是红色错误
    expect(screen.getByText('未启用')).toBeInTheDocument()
    expect(screen.queryByText('有问题')).not.toBeInTheDocument()
  })

  it('「去配置」跳到对应设置页', async () => {
    mockedGet.mockResolvedValue(READY_DATA)
    renderPage()

    await waitFor(() => expect(screen.getByText('邮件 SMTP')).toBeInTheDocument())
    const links = screen.getAllByRole('button', { name: /去配置/ })
    expect(links.length).toBe(3)
    await userEvent.click(links[1])
    // 跳转目标由后端 link 字段决定，前端不硬编码
    expect(links[1]).toBeInTheDocument()
  })

  it('没有 link 的条目不给「去配置」按钮，避免点了没反应', async () => {
    mockedGet.mockResolvedValue(BLOCKED_DATA)
    renderPage()

    await waitFor(() => expect(screen.getByText('站点地址')).toBeInTheDocument())
    // 4 条里只有 3 条带 link（站点地址由 .env 配置，后台里改不了）
    expect(screen.getAllByRole('button', { name: /去配置/ }).length).toBe(3)
  })

  it('接口失败时给出可操作提示', async () => {
    mockedGet.mockRejectedValue(new Error('请求失败'))
    renderPage()

    await waitFor(() => expect(screen.getByText(/体检接口没能返回结果/)).toBeInTheDocument())
    expect(screen.getByText(/docker compose ps/)).toBeInTheDocument()
  })
})
