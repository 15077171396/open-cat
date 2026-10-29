import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Descriptions, Divider, Input, Modal, Space, Tag, Typography } from 'antd'
import { apiGet, apiPost } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'

/**
 * 安全设置：二次验证（TOTP）+ 退出全部设备。
 *
 * 为什么后台需要第二道门：这里能改全站内容、看全量手机号邮箱、改积分、批删账号，
 * 而此前**只凭一个密码**。密码泄露（撞库/复用/钓鱼）没有任何兜底。
 *
 * 三条 UI 约定（改之前先读）：
 * - **恢复码只显示一次**：库里只存哈希，弹窗里必须明确「现在不保存就再也看不到」；
 * - **绑定分两步**：拿到密钥后必须输入一次验证码才算启用（后端也是这么实现的），
 *   所以按钮文案是「验证并启用」而不是「保存」；
 * - 关闭二次验证**必须输入密码**：防止拿到已登录设备的人直接摘掉第二因子。
 */

interface TotpStatus {
  enabled?: boolean
  pending?: boolean
  recovery_remaining?: number
  recovery_should_regenerate?: boolean
}

interface SetupResult {
  secret?: string
  uri?: string
  recovery_codes?: string[]
}

export default function SecurityTab() {
  const [status, setStatus] = useState<TotpStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  // 绑定流程的中间态（拿到密钥但还没验证）
  const [setup, setSetup] = useState<SetupResult | null>(null)
  const [code, setCode] = useState('')
  const [disableOpen, setDisableOpen] = useState(false)
  const [password, setPassword] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setStatus(await apiGet<TotpStatus>('/auth/totp'))
    } catch (err) {
      toast.error(getErrorMessage(err, '二次验证状态加载失败'))
      setStatus(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const handleSetup = async () => {
    setBusy(true)
    try {
      const data = await apiPost<SetupResult>('/auth/totp/setup')
      setSetup(data)
      setCode('')
      toast.success('已生成密钥，请用验证器 App 扫码后输入验证码')
    } catch (err) {
      toast.error(getErrorMessage(err, '生成绑定信息失败'))
    } finally {
      setBusy(false)
    }
  }

  const handleEnable = async () => {
    if (!code.trim()) {
      toast.error('请输入验证器 App 中的 6 位验证码')
      return
    }
    setBusy(true)
    try {
      await apiPost('/auth/totp/enable', { code: code.trim() })
      toast.success('二次验证已启用，之后登录需要输入验证码')
      setSetup(null)
      setCode('')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '启用失败'))
    } finally {
      setBusy(false)
    }
  }

  const handleDisable = async () => {
    if (!password) {
      toast.error('请输入登录密码')
      return
    }
    setBusy(true)
    try {
      await apiPost('/auth/totp/disable', { password })
      toast.success('二次验证已关闭')
      setDisableOpen(false)
      setPassword('')
      await load()
    } catch (err) {
      toast.error(getErrorMessage(err, '关闭失败'))
    } finally {
      setBusy(false)
    }
  }

  const handleRegenerate = async () => {
    if (!code.trim()) {
      toast.error('请先输入当前验证码')
      return
    }
    setBusy(true)
    try {
      const data = await apiPost<{ recovery_codes?: string[] }>('/auth/totp/recovery', { code: code.trim() })
      setSetup({ recovery_codes: data.recovery_codes })
      setCode('')
      toast.success('恢复码已重新生成（旧的已作废）')
    } catch (err) {
      toast.error(getErrorMessage(err, '重新生成失败'))
    } finally {
      setBusy(false)
    }
  }

  const handleLogoutAll = () => {
    Modal.confirm({
      title: '退出全部设备',
      content: '所有已登录的设备（含当前这台）都会被踢下线，需要重新登录。怀疑密码泄露时用它一键收口。',
      okText: '全部退出',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await apiPost('/auth/logout-all')
          toast.success('已退出全部设备，请重新登录')
          // 令牌版本号已提升，本地状态必然失效 —— 直接整页跳转最干净
          window.location.href = '/admin/login'
        } catch (err) {
          toast.error(getErrorMessage(err, '操作失败'))
        }
      },
    })
  }

  const enabled = status?.enabled === true

  return (
    <div style={{ maxWidth: 720 }}>
      <Typography.Title level={5} style={{ marginTop: 0 }}>
        二次验证（TOTP）
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        用手机验证器 App（1Password / Google Authenticator / 微软 Authenticator）扫描密钥，
        登录时除密码外还要输入 6 位动态码。开启后即使密码泄露，对方也进不来。
      </Typography.Paragraph>

      <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
        <Descriptions.Item label="当前状态">
          {loading ? (
            <Typography.Text type="secondary">加载中…</Typography.Text>
          ) : enabled ? (
            <Tag color="success">已启用</Tag>
          ) : status?.pending ? (
            <Tag color="warning">已生成密钥但未验证（未启用）</Tag>
          ) : (
            <Tag>未启用</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="剩余恢复码">
          {enabled ? (
            <Space size={8}>
              <span>{status?.recovery_remaining ?? 0} 个</span>
              {status?.recovery_should_regenerate ? <Tag color="warning">建议重新生成</Tag> : null}
            </Space>
          ) : (
            <Typography.Text type="secondary">启用后生成</Typography.Text>
          )}
        </Descriptions.Item>
      </Descriptions>

      {setup?.secret ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          title="请立即完成绑定"
          description={
            <div style={{ fontSize: 13 }}>
              <div style={{ marginBottom: 8 }}>
                在验证器 App 里选择「手动输入密钥」，粘贴下面这串（区分大小写不敏感，空格会被忽略）：
              </div>
              <Typography.Paragraph copyable style={{ fontFamily: 'monospace', marginBottom: 8 }}>
                {setup.secret}
              </Typography.Paragraph>
              <div style={{ marginBottom: 8 }}>
                或直接打开链接：
                <Typography.Link href={setup.uri} target="_blank" rel="noreferrer" style={{ marginLeft: 4 }}>
                  otpauth 链接
                </Typography.Link>
              </div>
              <Space.Compact style={{ width: 280 }}>
                <Input
                  placeholder="输入 App 显示的 6 位码"
                  value={code}
                  maxLength={12}
                  onChange={(e) => setCode(e.target.value)}
                />
                <Button type="primary" loading={busy} onClick={handleEnable}>
                  验证并启用
                </Button>
              </Space.Compact>
            </div>
          }
        />
      ) : null}

      {/* 恢复码只返回一次：必须让用户当场抄走，否则手机丢了只能由管理员改库救回 */}
      {setup?.recovery_codes?.length ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          title="恢复码（只显示这一次，请立即保存）"
          description={
            <div>
              <div style={{ fontSize: 13, marginBottom: 8 }}>
                手机丢失时用其中任意一个登录（每个只能用一次，登录后该码即失效）。
                库里只存哈希，**关掉这个页面就再也看不到**。
              </div>
              <Space wrap size={8}>
                {setup.recovery_codes.map((item) => (
                  <Tag key={item} style={{ fontFamily: 'monospace', fontSize: 13 }}>
                    {item}
                  </Tag>
                ))}
              </Space>
              <div style={{ marginTop: 8 }}>
                <Typography.Text copyable={{ text: setup.recovery_codes.join('\n') }} type="secondary">
                  一键复制全部
                </Typography.Text>
              </div>
            </div>
          }
        />
      ) : null}

      <Space wrap size={12}>
        {!enabled ? (
          <Button type="primary" loading={busy} onClick={handleSetup} disabled={!!setup?.secret}>
            {setup?.secret ? '已生成密钥（见上方）' : '开始绑定'}
          </Button>
        ) : (
          <>
            <Button loading={busy} onClick={handleRegenerate} disabled={!code.trim()}>
              重新生成恢复码
            </Button>
            <Button danger onClick={() => setDisableOpen(true)}>
              关闭二次验证
            </Button>
          </>
        )}
      </Space>

      {enabled && !setup?.secret ? (
        <div style={{ marginTop: 12 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            重新生成恢复码需要先输入当前验证码：
          </Typography.Text>
          <div style={{ marginTop: 6 }}>
            <Input
              style={{ width: 200 }}
              placeholder="6 位验证码"
              value={code}
              maxLength={12}
              onChange={(e) => setCode(e.target.value)}
            />
          </div>
        </div>
      ) : null}

      <Divider />

      <Typography.Title level={5}>会话</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        本站每个账号只保留一个登录凭据（多设备共享同一条），因此这里提供的是
        「一键把所有设备踢下线」，而不是逐台管理。
      </Typography.Paragraph>
      <Button danger onClick={handleLogoutAll}>
        退出全部设备
      </Button>

      <Modal
        title="关闭二次验证"
        open={disableOpen}
        onCancel={() => {
          setDisableOpen(false)
          setPassword('')
        }}
        onOk={handleDisable}
        confirmLoading={busy}
        okText="确认关闭"
        okType="danger"
        cancelText="取消"
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          关闭后登录只需要密码。请确认你仍然能保证密码强度。
        </Typography.Paragraph>
        <Input.Password
          placeholder="输入登录密码以确认"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </Modal>
    </div>
  )
}
