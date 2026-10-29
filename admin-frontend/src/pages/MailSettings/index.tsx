import { useState } from 'react'
import { Button, Input, Space, Tag, Typography } from 'antd'
import {
  CheckCircleFilled,
  ExclamationCircleFilled,
  MailOutlined,
  SendOutlined,
} from '@ant-design/icons'
import { apiPost } from '../../api/client'
import SettingsEditor from '../../components/settings/SettingsEditor'
import FieldStack from '../../components/settings/FieldStack'
import type { SettingsPayload } from '../../components/settings/types'
import { useAuthStore } from '../../store/authStore'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import '../../components/settings/settingsKit.css'

/** 后端 GET /admin/mail-settings 的通道状态 */
interface MailChannel {
  ready: boolean
  /** `host:port`，未配置时为空串 */
  endpoint: string
  tls: 'ssl' | 'starttls' | 'none'
  sender: string
  missing: string[]
  issues: string[]
}

interface MailPayload extends SettingsPayload {
  channel: MailChannel
}

const TLS_LABEL: Record<MailChannel['tls'], string> = {
  ssl: 'SSL（465 隐式加密）',
  starttls: 'STARTTLS',
  none: '不加密',
}

/**
 * 测试发信面板。
 *
 * 这块是整个模块存在的核心理由：SMTP 配错的表现是「用户说收不到验证码」，
 * 而管理员面对表单只能猜。这里用**与真实验证码完全相同的连接参数**发一封，
 * 成功/失败都当场给出原因（连不上 / 认证失败 / 超时）。
 *
 * 有未保存改动时禁用：否则测的是「已保存的旧配置」，会让人误以为改对了。
 */
function TestMailPanel({
  channel,
  disabled,
  onSent,
}: {
  channel: MailChannel
  disabled: boolean
  onSent: () => void
}) {
  const account = useAuthStore((s) => s.user?.account ?? '')
  // 管理员通常用邮箱登录：默认收件人填自己，少一次输入
  const [to, setTo] = useState(account.includes('@') ? account : '')
  const [sending, setSending] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  const send = async () => {
    const address = to.trim()
    if (!address) {
      toast.warning('请填写收件邮箱')
      return
    }
    setSending(true)
    setResult(null)
    try {
      await apiPost('/admin/mail-settings/test', { to: address })
      setResult({ ok: true, text: `已发送至 ${address}，请到收件箱（含垃圾箱）确认` })
      onSent()
    } catch (err) {
      setResult({ ok: false, text: getErrorMessage(err, '发送失败') })
    } finally {
      setSending(false)
    }
  }

  return (
    <section className="settings-section">
      <div className="settings-section__head">
        <span className="settings-section__index">02</span>
        <span className="settings-section__title">发送测试邮件</span>
        {!channel.ready && <Tag color="orange">通道未配齐</Tag>}
      </div>
      <p className="settings-section__desc">
        用与注册验证码相同的连接参数发一封测试邮件。改动请先保存再测，否则测的是已保存的旧配置。
      </p>
      <Space.Compact style={{ width: 420 }}>
        <Input
          value={to}
          placeholder="收件邮箱，如 ops@example.com"
          onChange={(e) => setTo(e.target.value)}
          disabled={sending}
          onPressEnter={() => void send()}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          loading={sending}
          disabled={disabled || !channel.ready}
          onClick={() => void send()}
        >
          发送
        </Button>
      </Space.Compact>
      {disabled && (
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
          有未保存的修改，保存后才能测试。
        </Typography.Text>
      )}
      {result && (
        <div
          className={`settings-banner ${result.ok ? 'settings-banner--info' : 'settings-banner--danger'}`}
          style={{ marginTop: 12 }}
        >
          {result.ok ? (
            <CheckCircleFilled className="settings-banner__icon" />
          ) : (
            <ExclamationCircleFilled className="settings-banner__icon settings-banner__icon--danger" />
          )}
          <div>{result.text}</div>
        </div>
      )}
    </section>
  )
}

/**
 * 邮件通知：SMTP 通道（验证码发信）。
 *
 * 2026-10-27 从「系统设置 → 邮件通知」抽出。抽出的理由不是配置项多（7 项），
 * 而是它**默认就是坏的**：不填 SMTP，生产环境发验证码直接失败，
 * 用户点「获取验证码」只看到一句「发送失败」—— 原表单无法回答「现在能不能发出去」。
 * 所以这页除了 7 个字段，还有：通道状态（缺哪几项 / 有没有加密）与一键测试发信。
 */
export default function MailSettingsPage() {
  return (
    <SettingsEditor<MailPayload>
      endpoint="/admin/mail-settings"
      title="邮件通知"
      icon={<MailOutlined />}
      savePerm="mail_setting:update"
      // SMTP 参数经 runtime_config 注入到 settings，后端保存后立即应用（其余 worker 60s 内跟上）
      saveSuccessText="邮件通道配置已保存，可立即发送测试邮件"
      loadErrorText="加载邮件通道配置失败"
    >
      {({ groups, draft, dirtyGroups, isDirty, setValue, payload, reload }) => {
        const channel = payload.channel
        return (
          <>
            <div
              className={`settings-banner ${
                channel.ready ? 'settings-banner--info' : 'settings-banner--danger'
              }`}
            >
              {channel.ready ? (
                <CheckCircleFilled className="settings-banner__icon" />
              ) : (
                <ExclamationCircleFilled className="settings-banner__icon settings-banner__icon--danger" />
              )}
              <div>
                <div>
                  {channel.ready ? '邮件通道已就绪' : '邮件通道未配齐：生产环境发不出验证码'}
                  {channel.endpoint ? ` · ${channel.endpoint}` : ''}
                  {channel.sender ? ` · 发件人 ${channel.sender}` : ''}
                  {` · ${TLS_LABEL[channel.tls]}`}
                </div>
                {channel.issues.map((issue) => (
                  <div key={issue} style={{ marginTop: 4 }}>
                    {issue}
                  </div>
                ))}
              </div>
            </div>

            {groups.map((g, index) => (
              <section className="settings-section" key={g.key}>
                <div className="settings-section__head">
                  <span className="settings-section__index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="settings-section__title">{g.label}</span>
                  {dirtyGroups.has(g.key) && <Tag color="orange">未保存</Tag>}
                </div>
                <p className="settings-section__desc">{g.description}</p>
                <FieldStack items={g.items} draft={draft} setValue={setValue} />
              </section>
            ))}

            <TestMailPanel channel={channel} disabled={isDirty} onSent={reload} />
          </>
        )
      }}
    </SettingsEditor>
  )
}
