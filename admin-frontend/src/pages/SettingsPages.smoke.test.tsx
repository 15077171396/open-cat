/**
 * 设置类页面的「能渲染」冒烟测试。
 *
 * 为什么值得留：这些页面都是 `lazy()` 加载的，渲染期一抛异常，用户看到的就是错误边界 /
 * 白屏（2026-10-27 真的出现过一次「页面发生异常」）。单测能在合代码前抓到
 * 「一进去就炸」这一类问题，不必等人点进去才发现。
 *
 * 断言刻意只用「整页文本包含」：这些页面里同一个文案常常同时出现在导航与标题、
 * 卡片与区块说明里，用 `getByText` 做全等匹配只会得到「Found multiple elements」。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => {
  const get = vi.fn()
  return {
    default: { get, put: vi.fn(async () => ({ data: {} })), post: vi.fn(async () => ({})) },
    apiGet: vi.fn(async () => ({})),
    apiPost: vi.fn(async () => ({})),
    apiPut: vi.fn(async () => ({})),
    apiDelete: vi.fn(async () => ({})),
  }
})

vi.mock('../components/QuillEditor', () => ({
  default: ({ placeholder }: { placeholder?: string }) => (
    <textarea aria-label={placeholder || 'editor'} readOnly />
  ),
}))

vi.mock('../utils/upload', () => ({ uploadImage: vi.fn(async () => ({ url: '' })) }))

import client, { apiGet, apiPost } from '../api/client'
import { useAuthStore } from '../store/authStore'
import AiModelsPage from './AiModels'
import LegalDocumentsPage from './LegalDocuments'
import LoginSettingsPage from './LoginSettings'
import MailSettingsPage from './MailSettings'
import PaymentSettingsPage from './PaymentSettings'
import SettingsPage from './Settings'

const get = client.get as unknown as ReturnType<typeof vi.fn>
const getRaw = apiGet as unknown as ReturnType<typeof vi.fn>
const postRaw = apiPost as unknown as ReturnType<typeof vi.fn>

const def = (key: string, label: string, type: string, group: string, groupLabel: string) => ({
  key,
  label,
  type,
  group,
  group_label: groupLabel,
  default: '',
})

const settingsPayload = {
  definitions: [
    def('site_name', '平台名称', 'text', 'basic', '基础设置'),
    def('trial_days', '试用天数', 'number', 'business', '业务规则'),
  ],
  groups: [
    { key: 'basic', label: '基础设置', description: '站点基础信息', surface: 'settings' },
    { key: 'business', label: '业务规则', description: '业务参数', surface: 'settings' },
  ],
  values: {},
}

/** 渲染并等待整页文本出现某段内容 */
async function expectText(node: React.ReactNode, text: string) {
  const { container } = render(<MemoryRouter>{node}</MemoryRouter>)
  await waitFor(() => expect(container.textContent || '').toContain(text))
}

describe('设置类页面渲染冒烟', () => {
  beforeEach(() => {
    get.mockReset()
    getRaw.mockReset()
    // 按钮级操作都在 PermGuard 后面：不给权限就渲染不出来（这是对的），
    // 但冒烟测试要验的正是「点得动」，所以这里当作超管
    useAuthStore.setState({ isSuper: true, permissions: null })
  })

  it('系统设置：左侧导航 + 首个分组表单', async () => {
    get.mockResolvedValue({ code: 0, data: settingsPayload })
    await expectText(<SettingsPage />, '平台名称')
  })

  it('邮件通知：通道状态 + 测试发信面板', async () => {
    get.mockResolvedValue({
      code: 0,
      data: {
        definitions: [def('smtp_host', 'SMTP 服务器', 'text', 'mail', 'SMTP 通道')],
        groups: [{ key: 'mail', label: 'SMTP 通道', description: '验证码发信' }],
        values: {},
        channel: {
          ready: false,
          endpoint: '',
          tls: 'none',
          sender: '',
          missing: ['SMTP 服务器'],
          issues: ['还缺 1 项'],
        },
      },
    })
    await expectText(<MailSettingsPage />, '发送测试邮件')
  })

  it('模型管理：成本护栏区块（含用量与剩余额度）', async () => {
    getRaw.mockImplementation(async (url: string) => {
      if (url === '/admin/ai-models') {
        return {
          items: [
            {
              id: 1,
              code: 'deepseek-chat',
              display_name: 'DeepSeek 对话',
              provider: 'deepseek',
              provider_label: 'DeepSeek',
              model_name: 'deepseek-chat',
              effective_model_name: 'deepseek-chat',
              base_url: '',
              effective_base_url: 'https://api.deepseek.com/v1',
              api_key_source: 'env',
              api_key_masked: '',
              input_price: 1,
              output_price: 2,
              is_enabled: true,
              is_default: true,
              sort_order: 0,
              remark: '',
              tool_refs: 0,
              created_at: null,
              updated_at: null,
            },
          ],
          providers: [{ name: 'deepseek', display_name: 'DeepSeek', env_prefix: 'DEEPSEEK_' }],
          total: 1,
        }
      }
      return {
        definitions: [
          def('ai_global_daily_limit', '全站日调用上限', 'number', 'ai', '全站成本护栏'),
        ],
        groups: [{ key: 'ai', label: '全站成本护栏' }],
        values: {},
        quota: {
          today_calls: 12,
          month_calls: 340,
          today_tokens: 1000,
          month_tokens: 20000,
          today_blocked: 0,
          daily_limit: 1000,
          monthly_limit: 50000,
          remaining_today: 988,
          remaining_month: 49660,
          today_cost: 0.12,
          month_cost: 3.4,
          price_configured: true,
          priced_models: 1,
        },
      }
    })
    const { container } = render(
      <MemoryRouter>
        <AiModelsPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(container.textContent || '').toContain('988'))

    // 「测试连接」：结果要就地显示在模型行里（失败也是 200，靠 ok 字段区分）
    postRaw.mockResolvedValue({ ok: true, latency_ms: 120, reply: 'pong' })
    fireEvent.click(screen.getByRole('button', { name: /测试/ }))
    await waitFor(() => expect(container.textContent || '').toContain('连通 120ms'))
  })

  it('登录管理：默认落在渠道状态，点导航可切到短信通道', async () => {
    get.mockResolvedValue({
      code: 0,
      data: {
        definitions: [
          def('phone_login_enabled', '手机号登录', 'bool', 'auth_phone', '手机号验证码登录'),
          def('sms_provider', '短信服务商', 'select', 'auth_sms', '短信通道'),
          def('sms_tencent_secret_id', '腾讯云 SecretId', 'text', 'auth_sms', '短信通道'),
        ],
        groups: [
          { key: 'auth_phone', label: '手机号验证码登录', description: '' },
          { key: 'auth_sms', label: '短信通道', description: '' },
        ],
        values: {},
        channels: [
          { key: 'email', label: '邮箱密码登录', enabled: true, ready: true, detail: '', issues: [] },
          {
            key: 'phone',
            label: '手机号验证码登录',
            enabled: false,
            ready: false,
            detail: '短信通道：Mock',
            issues: [],
          },
        ],
      },
    })
    const { container } = render(
      <MemoryRouter>
        <LoginSettingsPage />
      </MemoryRouter>,
    )
    // 默认：状态卡
    await waitFor(() => expect(container.textContent || '').toContain('邮箱密码登录'))
    expect(container.textContent).toContain('渠道状态')

    // 切到「短信通道」：右侧换成该组字段 + 按服务商折叠的参数
    fireEvent.click(screen.getByRole('button', { name: '短信通道' }))
    await waitFor(() => expect(container.textContent || '').toContain('腾讯云 SMS 参数'))
    expect(container.textContent).not.toContain('邮箱密码登录')
  })

  it('法律文档：文档导航（前台位置提示）', async () => {
    get.mockResolvedValue({
      code: 0,
      data: {
        definitions: [def('legal_privacy', '隐私政策', 'richtext', 'legal', '法律文档')],
        groups: [{ key: 'legal', label: '法律文档', description: '' }],
        values: {},
        documents: [
          {
            key: 'legal_privacy',
            label: '隐私政策',
            type: 'privacy',
            is_custom: false,
            length: 0,
            updated_at: null,
            public_hint: '前台页脚 → 隐私政策',
            public_api: '/api/v1/settings/legal/privacy',
          },
        ],
      },
    })
    await expectText(<LegalDocumentsPage />, '前台页脚')
  })

  it('支付与渠道：总开关卡与渠道卡', async () => {
    get.mockResolvedValue({
      code: 0,
      data: {
        definitions: [
          def('payment_enabled', '在线支付总开关', 'bool', 'payment_general', '支付开关与订单'),
        ],
        groups: [
          { key: 'payment_general', label: '支付开关与订单', description: '总开关' },
          { key: 'payment_wechat', label: '微信支付', description: 'v3 Native' },
          { key: 'payment_alipay', label: '支付宝', description: '电脑网站支付' },
        ],
        values: {},
        enabled: false,
        env: 'development',
        channels: [
          {
            key: 'mock',
            label: '模拟支付',
            enabled: false,
            ready: false,
            detail: '仅本地',
            issues: [],
          },
        ],
        warnings: [],
      },
    })
    // 默认停在「渠道状态」：导航里三组配置都在，右侧是状态卡
    await expectText(<PaymentSettingsPage />, '在线支付（总开关）')
  })
})
