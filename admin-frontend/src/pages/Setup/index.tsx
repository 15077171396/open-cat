import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Form, Input, Result, Space, Spin, Tag, Typography } from 'antd'
import { LockOutlined, RocketOutlined, SafetyOutlined, UserOutlined } from '@ant-design/icons'
import { apiGet, apiPost } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { BRAND } from '../../theme'
import { toast } from '../../utils/toast'

interface SetupStatus {
  needs_setup: boolean
  site_base_url: string
  storage_driver: string
}

interface SetupFormValues {
  account: string
  password: string
  confirm: string
  nickname?: string
}

/**
 * 安装向导：创建**第一个**管理员。
 *
 * 为什么需要这一页：原流程要求跑 `seed_data.py` 再手动抄日志里的随机口令，
 * 陌生人根本走不到「登录」这一步。这里把首次初始化变成一次网页操作。
 *
 * 三条约定：
 * - 后端 `/setup/init` 一旦存在管理员就返回 409，因此本页只对「空库」有意义；
 * - 打开时先问 `/setup/status`，已初始化就直接转登录页，避免留下一个永远 409 的入口；
 * - 只创建账号，**不收集**存储/邮件/AI 等配置——那些在后台「系统设置」里有掩码与
 *   独立权限码，混进向导会变成第二个不受审计的配置入口。
 */
export default function Setup() {
  const navigate = useNavigate()
  const [form] = Form.useForm<SetupFormValues>()
  const [checking, setChecking] = useState(true)
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiGet<SetupStatus>('/setup/status')
        if (alive) setStatus(data)
      } catch (err) {
        // 状态查不到时不要卡死在 loading：让用户看到「服务不可用」比空转更可诊断
        if (alive) setError(getErrorMessage(err, '无法连接后端服务，请确认服务已启动'))
      } finally {
        if (alive) setChecking(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  const handleSubmit = async (values: SetupFormValues) => {
    setLoading(true)
    setError('')
    try {
      await apiPost('/setup/init', {
        account: values.account.trim(),
        password: values.password,
        nickname: values.nickname?.trim() || null,
      })
      toast.success('初始化完成，请登录')
      navigate('/login', { replace: true })
    } catch (err) {
      setError(getErrorMessage(err, '初始化失败，请稍后重试'))
    } finally {
      setLoading(false)
    }
  }

  const shell = (children: React.ReactNode) => (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: BRAND.gradient,
        padding: 24,
      }}
    >
      <Card style={{ maxWidth: 440, width: '100%', borderRadius: 16 }}>{children}</Card>
    </div>
  )

  if (checking) {
    return shell(
      <div style={{ padding: '48px 0', textAlign: 'center' }}>
        <Spin />
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
          正在检查初始化状态…
        </Typography.Paragraph>
      </div>,
    )
  }

  // 已初始化：不再展示表单，直接给出登录入口（表单提交也只会拿到 409）
  if (status && !status.needs_setup) {
    return shell(
      <Result
        status="success"
        title="系统已完成初始化"
        subTitle="管理员账号已存在，请直接登录后台。"
        extra={
          <Button type="primary" onClick={() => navigate('/login', { replace: true })}>
            前往登录
          </Button>
        }
      />,
    )
  }

  return shell(
    <>
      <Space orientation="vertical" align="center" style={{ width: '100%', marginBottom: 24 }}>
        <Tag color="geekblue" icon={<RocketOutlined />}>
          首次部署
        </Tag>
        <Typography.Title level={3} style={{ margin: 0 }}>
          初始化管理后台
        </Typography.Title>
        <Typography.Text type="secondary">创建第一个管理员账号</Typography.Text>
      </Space>

      {error && (
        <Alert
          title={error}
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          // 连接失败时给出可操作提示，而不是只报错
          description={
            error.includes('无法连接') ? '检查后端容器是否已启动：docker compose ps' : undefined
          }
        />
      )}

      <Form form={form} layout="vertical" size="large" onFinish={handleSubmit}>
        <Form.Item
          label="管理员账号"
          name="account"
          rules={[
            { required: true, message: '请输入账号' },
            { min: 3, max: 100, message: '账号长度 3–100 位' },
          ]}
          extra="邮箱或用户名，登录时用这个"
        >
          <Input prefix={<UserOutlined />} placeholder="admin@example.com" autoComplete="username" />
        </Form.Item>

        <Form.Item
          label="登录口令"
          name="password"
          rules={[
            { required: true, message: '请输入口令' },
            { min: 8, max: 50, message: '口令长度 8–50 位' },
          ]}
          extra="至少 8 位；建议用随机串（openssl rand -base64 12）"
        >
          <Input.Password prefix={<LockOutlined />} placeholder="设置登录口令" autoComplete="new-password" />
        </Form.Item>

        <Form.Item
          label="确认口令"
          name="confirm"
          dependencies={['password']}
          rules={[
            { required: true, message: '请再次输入口令' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('password') === value) return Promise.resolve()
                return Promise.reject(new Error('两次输入的口令不一致'))
              },
            }),
          ]}
        >
          <Input.Password prefix={<SafetyOutlined />} placeholder="再次输入口令" autoComplete="new-password" />
        </Form.Item>

        <Form.Item label="昵称" name="nickname" extra="可选，显示在右上角">
          <Input placeholder="例如：站长" maxLength={50} />
        </Form.Item>

        <Form.Item style={{ marginBottom: 12 }}>
          <Button type="primary" htmlType="submit" loading={loading} block>
            {loading ? '正在创建…' : '创建管理员并进入登录'}
          </Button>
        </Form.Item>
      </Form>

      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
        此页面只在系统内还没有管理员时可用；创建成功后会自动关闭。
        {status?.storage_driver === 'local' && ' 当前使用本地存储，上传文件不需要任何云账号。'}
      </Typography.Paragraph>
    </>,
  )
}
