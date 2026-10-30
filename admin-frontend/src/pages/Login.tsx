import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, Input, Button, Typography, Alert, Segmented } from 'antd'
import {
  BulbOutlined,
  LockOutlined,
  MobileOutlined,
  SafetyOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useAuthStore } from '../store/authStore'
import { getErrorMessage, getErrorData } from '../utils/error'
import { apiGet } from '../api/client'
import { LOCALE_LABELS, SUPPORTED_LOCALES, useI18n } from '../i18n'
import type { Locale } from '../i18n'

interface LoginFormValues {
  account: string
  password: string
  totpCode?: string
}

/**
 * 本地开发预填账号密码，省去每次手输。
 *
 * **只在 `MODE=development` 生效**：生产构建时 `import.meta.env.MODE` 被静态替换成
 * `'production'`，整个开发分支（含默认凭据）会被压缩器摇掉，不会进入公开产物；
 * 测试环境（MODE=test）同样为空。要换开发账号，在 gitignore 的
 * `admin-frontend/.env.local` 配 `VITE_LOGIN_PREFILL_ACCOUNT` / `VITE_LOGIN_PREFILL_PASSWORD`。
 */
const LOGIN_PREFILL =
  import.meta.env.MODE === 'development'
    ? {
        account: import.meta.env.VITE_LOGIN_PREFILL_ACCOUNT ?? 'admin@example.com',
        password: import.meta.env.VITE_LOGIN_PREFILL_PASSWORD ?? 'Admin@123456',
      }
    : { account: '', password: '' }

export default function Login() {
  const navigate = useNavigate()
  const { login } = useAuthStore()
  const { locale, setLocale, t } = useI18n()
  const [form] = Form.useForm<LoginFormValues>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  // 二次验证是**第二步**：后端在密码正确但缺码时回 401 + data.totp_required，
  // 这里据此展开验证码输入框（不能靠匹配 message 文案，文案一改就静默失效）
  const [totpRequired, setTotpRequired] = useState(false)

  // 空库时把用户直接送到安装向导：否则第一个管理员永远进不来
  // （后端 /setup/init 一旦有管理员就 409，所以这里只在 needs_setup 时跳）
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiGet<{ needs_setup: boolean }>('/setup/status')
        if (alive && data.needs_setup) navigate('/setup', { replace: true })
      } catch {
        // 状态查不到就留在登录页：连不上后端时跳转到向导也只会看到另一个报错
      }
    })()
    return () => {
      alive = false
    }
  }, [navigate])

  const handleSubmit = async (values: LoginFormValues) => {
    const { account, password, totpCode } = values
    if (!account || !password) {
      setError(t('login.missing'))
      return
    }
    if (totpRequired && !totpCode) {
      setError(t('login.totpMissing'))
      return
    }
    setLoading(true)
    setError('')
    try {
      await login(account, password, totpRequired ? totpCode : undefined)
      navigate('/')
    } catch (err) {
      const data = getErrorData<{ totp_required?: boolean }>(err)
      if (data?.totp_required) setTotpRequired(true)
      setError(getErrorMessage(err, t('login.failed')))
    } finally {
      setLoading(false)
    }
  }

  const year = new Date().getFullYear()

  return (
    <div className="login-page">
      <aside className="login-brand" aria-hidden="true">
        <div className="login-brand__logo">
          <img src={`${import.meta.env.BASE_URL}favicon-64.png`} alt="" />
          <span>{t('login.title')}</span>
        </div>

        <div className="login-brand__copy">
          <h1>{t('login.heroTitle')}</h1>
          <p>{t('login.heroDesc')}</p>
        </div>

        <ul className="login-brand__features">
          <li>
            <SafetyOutlined />
            <div>
              <strong>{t('login.feature1Title')}</strong>
              <span>{t('login.feature1Desc')}</span>
            </div>
          </li>
          <li>
            <BulbOutlined />
            <div>
              <strong>{t('login.feature2Title')}</strong>
              <span>{t('login.feature2Desc')}</span>
            </div>
          </li>
          <li>
            <MobileOutlined />
            <div>
              <strong>{t('login.feature3Title')}</strong>
              <span>{t('login.feature3Desc')}</span>
            </div>
          </li>
        </ul>

        <div className="login-brand__footer">© {year} AI学习猫</div>
      </aside>

      <main className="login-main">
        {/* 登录页在后台外壳之外，语言入口要单独给：选中结果同样持久化，进后台后沿用 */}
        <div className="login-main__lang">
          <Segmented
            size="small"
            value={locale}
            onChange={(value) => setLocale(value as Locale)}
            options={SUPPORTED_LOCALES.map((item) => ({
              label: LOCALE_LABELS[item],
              value: item,
            }))}
          />
        </div>

        <div className="login-card">
          <div className="login-card__head">
            <img
              className="login-card__logo"
              src={`${import.meta.env.BASE_URL}favicon-64.png`}
              alt=""
            />
            <Typography.Title level={3} style={{ margin: 0 }}>
              {t('login.welcome')}
            </Typography.Title>
            <Typography.Text type="secondary">{t('login.subtitle')}</Typography.Text>
          </div>

          <Form
            form={form}
            layout="vertical"
            onFinish={handleSubmit}
            size="large"
            initialValues={LOGIN_PREFILL}
          >
            <Form.Item
              label={t('login.account')}
              name="account"
              rules={[{ required: true, message: t('login.accountRequired') }]}
            >
              <Input
                prefix={<UserOutlined />}
                placeholder={t('login.accountPlaceholder')}
                autoComplete="username"
              />
            </Form.Item>

            <Form.Item
              label={t('login.password')}
              name="password"
              rules={[{ required: true, message: t('login.passwordRequired') }]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder={t('login.passwordPlaceholder')}
                autoComplete="current-password"
              />
            </Form.Item>

            {totpRequired && (
              <Form.Item
                label={t('login.totp')}
                name="totpCode"
                rules={[{ required: true, message: t('login.totpRequired') }]}
                extra={t('login.totpHint')}
              >
                <Input
                  prefix={<SafetyOutlined />}
                  placeholder={t('login.totpPlaceholder')}
                  autoComplete="one-time-code"
                  maxLength={12}
                />
              </Form.Item>
            )}

            {error && (
              <Form.Item style={{ marginBottom: 16 }}>
                <Alert title={error} type="error" showIcon />
              </Form.Item>
            )}

            <Form.Item>
              <Button type="primary" htmlType="submit" loading={loading} block>
                {loading ? t('login.submitting') : t('login.submit')}
              </Button>
            </Form.Item>
          </Form>
        </div>

        <div className="login-main__footer">© {year} AI学习猫</div>
      </main>
    </div>
  )
}
