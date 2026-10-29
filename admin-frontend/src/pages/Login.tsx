import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Form, Input, Button, Typography, Alert, Space, Tag } from 'antd'
import { LockOutlined, UserOutlined, SafetyOutlined } from '@ant-design/icons'
import { useAuthStore } from '../store/authStore'
import { getErrorMessage, getErrorData } from '../utils/error'
import { apiGet } from '../api/client'

interface LoginFormValues {
  account: string
  password: string
  totpCode?: string
}

export default function Login() {
  const navigate = useNavigate()
  const { login } = useAuthStore()
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
    if (!account || !password) { setError('请填写账号和密码'); return }
    if (totpRequired && !totpCode) { setError('请输入验证器 App 中的 6 位验证码'); return }
    setLoading(true)
    setError('')
    try {
      await login(account, password, totpRequired ? totpCode : undefined)
      navigate('/')
    } catch (err) {
      const data = getErrorData<{ totp_required?: boolean }>(err)
      if (data?.totp_required) setTotpRequired(true)
      setError(getErrorMessage(err, '登录失败'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, #1e3a8a 0%, #312e81 100%)',
      padding: '24px',
    }}>
      <Card style={{
        maxWidth: 400,
        width: '100%',
        borderRadius: 16,
        boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
      }}>
        <Space orientation="vertical" align="center" style={{ width: '100%', marginBottom: 24 }}>
          <Tag color="geekblue" icon={<SafetyOutlined />}>管理员入口</Tag>
          <Typography.Title level={3} style={{ margin: 0 }}>AI学习猫 管理后台</Typography.Title>
          <Typography.Text type="secondary">仅限管理员账号登录</Typography.Text>
        </Space>

        <Form form={form} layout="vertical" onFinish={handleSubmit} size="large">
          <Form.Item
            label="账号"
            name="account"
            rules={[{ required: true, message: '请输入手机号或邮箱' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="手机号 / 邮箱" autoComplete="username" />
          </Form.Item>

          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" autoComplete="current-password" />
          </Form.Item>

          {totpRequired && (
            <Form.Item
              label="二次验证码"
              name="totpCode"
              rules={[{ required: true, message: '请输入验证器 App 中的验证码' }]}
              extra="也可输入一次性的恢复码（形如 ABCDE-FGHJK）"
            >
              <Input
                prefix={<SafetyOutlined />}
                placeholder="6 位验证码 / 恢复码"
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
              {loading ? '登录中...' : '登录'}
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}
