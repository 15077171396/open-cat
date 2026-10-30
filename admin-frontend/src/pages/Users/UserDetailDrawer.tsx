import type { ComponentType, CSSProperties } from 'react'
import { Avatar, Button, Drawer, Descriptions, Timeline, Spin, Space, Tag, Typography } from 'antd'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  EditOutlined,
  HistoryOutlined,
  InfoCircleOutlined,
  MinusCircleOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
  WalletOutlined,
} from '@ant-design/icons'
import { POINT_TX_TYPE_MAP } from '../../constants'
import { formatDateTime } from '../../utils/datetime'
import type { AuthHistoryItem, PointTransaction, User } from '../../types'
import { ROLE_META } from './roleMeta'

export const AUTH_STATUS_MAP: Record<string, { color: string; text: string; icon: ComponentType<{ style?: CSSProperties }> }> = {
  approved: { color: 'success', text: '已认证', icon: CheckCircleOutlined },
  pending: { color: 'processing', text: '待审核', icon: ClockCircleOutlined },
  rejected: { color: 'error', text: '已驳回', icon: CloseCircleOutlined },
}

/** 将秒数格式化为可读时长（如 "1时30分"） */
export function formatDuration(seconds: number): string {
  if (!seconds) return '0分'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h}时${m}分` : `${m}分`
}

interface UserDetailDrawerProps {
  open: boolean
  loading: boolean
  user: User | null
  /** 最近积分流水（编辑保存后由父组件按需刷新） */
  pointTxs: PointTransaction[]
  pointTxsLoading: boolean
  onClose: () => void
  onEdit: () => void
}

/** 分区块标题（与后台编辑页的 `.adm-section` 同一套视觉） */
function DrawerSection({
  icon,
  title,
  children,
}: {
  icon: ComponentType<{ style?: CSSProperties }>
  title: string
  children: React.ReactNode
}) {
  const Icon = icon
  return (
    <div className="adm-section">
      <div className="adm-section__head">
        <span className="adm-section__icon"><Icon /></span>
        <span className="adm-section__title">{title}</span>
      </div>
      {children}
    </div>
  )
}

/** 用户详情抽屉：基本信息 + 星球认证 + 最近积分流水 + 认证历史 */
export default function UserDetailDrawer({
  open,
  loading,
  user,
  pointTxs,
  pointTxsLoading,
  onClose,
  onEdit,
}: UserDetailDrawerProps) {
  // 认证历史时间线渲染
  const renderAuthTimeline = () => {
    const history: AuthHistoryItem[] = user?.auth_history || []
    if (history.length === 0) {
      return <Typography.Text type="secondary">暂无认证记录</Typography.Text>
    }
    return (
      <Timeline
        items={history.map((ar: AuthHistoryItem) => {
          const cfg = AUTH_STATUS_MAP[ar.status] || { color: 'gray', text: ar.status, icon: MinusCircleOutlined }
          const color = ar.status === 'approved' ? 'green' : ar.status === 'pending' ? 'blue' : ar.status === 'rejected' ? 'red' : 'gray'
          return {
            color,
            content: (
              <div>
                <Space style={{ marginBottom: 4 }}>
                  <Tag color={cfg.color}>{cfg.text}</Tag>
                  <Typography.Text code>{ar.star_code}</Typography.Text>
                  {ar.vip_duration && <Typography.Text type="secondary">时长: {ar.vip_duration}</Typography.Text>}
                </Space>
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    提交: {formatDateTime(ar.created_at)}
                  </Typography.Text>
                </div>
                {ar.reviewed_at && (
                  <div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      审核: {formatDateTime(ar.reviewed_at)}
                    </Typography.Text>
                  </div>
                )}
                {ar.reject_reason && (
                  <div>
                    <Typography.Text type="danger" style={{ fontSize: 12 }}>
                      驳回原因: {ar.reject_reason}
                    </Typography.Text>
                  </div>
                )}
              </div>
            ),
          }
        })}
      />
    )
  }

  const roleMeta = user ? ROLE_META[user.role] || { text: user.role, color: 'default' } : null

  return (
    <Drawer
      title="用户详情"
      open={open}
      onClose={onClose}
      size="large"
      extra={
        user ? (
          <Button type="primary" icon={<EditOutlined />} onClick={onEdit}>编辑用户</Button>
        ) : null
      }
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
      ) : user ? (
        <>
          {/* 头部身份区：头像 + 账号 + 角色/认证角标 */}
          <div className="adm-cell" style={{ marginBottom: 24 }}>
            <Avatar className="adm-avatar" size={64} icon={<UserOutlined />} src={user.avatar_url || undefined} />
            <div className="adm-cell__body">
              <div style={{ fontSize: 17, fontWeight: 600, color: '#1f1f1f', lineHeight: 1.3 }}>
                {user.account}
              </div>
              <Space style={{ marginTop: 6 }} size={6} wrap>
                <Tag color={roleMeta?.color}>{roleMeta?.text}</Tag>
                {user.auth_status && (
                  <Tag color={AUTH_STATUS_MAP[user.auth_status]?.color || 'default'}>
                    {AUTH_STATUS_MAP[user.auth_status]?.text || user.auth_status}
                  </Tag>
                )}
                {user.department_name && <Tag color="purple">{user.department_name}</Tag>}
              </Space>
            </div>
          </div>

          <DrawerSection icon={InfoCircleOutlined} title="基本信息">
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="用户 ID">{user.id}</Descriptions.Item>
              <Descriptions.Item label="账号">{user.account}</Descriptions.Item>
              <Descriptions.Item label="角色">
                <Tag color={roleMeta?.color}>{roleMeta?.text}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="注册时间">
                {formatDateTime(user.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="VIP 到期">
                {user.vip_expire_time ? user.vip_expire_time.slice(0, 10) : '未开通'}
              </Descriptions.Item>
              <Descriptions.Item label="免费时长">
                {user.role === 'vip' || user.role === 'admin'
                  ? '不限时'
                  : `${formatDuration(user.free_used_seconds)}（剩 ${formatDuration(user.free_remaining_seconds ?? 0)}）`}
              </Descriptions.Item>
              <Descriptions.Item label="积分余额">
                {user.points ?? 0} 分（用于运行「需积分」工具）
              </Descriptions.Item>
              {user.avatar_url && (
                <Descriptions.Item label="头像 URL">
                  <Typography.Text style={{ wordBreak: 'break-all' }} type="secondary">
                    {user.avatar_url}
                  </Typography.Text>
                </Descriptions.Item>
              )}
            </Descriptions>
          </DrawerSection>

          <DrawerSection icon={SafetyCertificateOutlined} title="星球认证">
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="当前状态">
                {user.auth_status ? (
                  <Tag color={AUTH_STATUS_MAP[user.auth_status]?.color || 'default'}>
                    {AUTH_STATUS_MAP[user.auth_status]?.text || user.auth_status}
                  </Tag>
                ) : (
                  <Typography.Text type="secondary">未申请</Typography.Text>
                )}
              </Descriptions.Item>
              {user.star_code && (
                <Descriptions.Item label="已认证星球号">
                  <Typography.Text code>{user.star_code}</Typography.Text>
                </Descriptions.Item>
              )}
              {user.pending_auth_code && (
                <Descriptions.Item label="待审核星球号">
                  <Typography.Text code>{user.pending_auth_code}</Typography.Text>
                </Descriptions.Item>
              )}
            </Descriptions>
          </DrawerSection>

          <DrawerSection icon={WalletOutlined} title="积分流水">
            {pointTxsLoading ? (
              <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
            ) : pointTxs.length === 0 ? (
              <Typography.Text type="secondary">暂无积分记录</Typography.Text>
            ) : (
              <div>
                {pointTxs.map((tx) => {
                  const cfg = POINT_TX_TYPE_MAP[tx.type] || { color: 'default', text: tx.type }
                  const isGain = tx.points > 0
                  return (
                    <div
                      key={tx.id}
                      style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0', borderBottom: '1px solid var(--app-border-light)' }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <Space size={8} wrap>
                          <Typography.Text ellipsis style={{ fontSize: 13 }}>{tx.description || cfg.text}</Typography.Text>
                          <Tag color={cfg.color} style={{ margin: 0, fontSize: 11, padding: '0 6px' }}>{cfg.text}</Tag>
                        </Space>
                        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 2 }}>
                          {formatDateTime(tx.created_at)}
                        </Typography.Text>
                      </div>
                      <Space size={12} style={{ flexShrink: 0 }}>
                        <Typography.Text strong style={{ color: isGain ? 'var(--app-success)' : 'var(--app-warning)', fontVariantNumeric: 'tabular-nums' }}>
                          {isGain ? `+${tx.points}` : tx.points}
                        </Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
                          余额 {tx.balance_after}
                        </Typography.Text>
                      </Space>
                    </div>
                  )
                })}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  仅显示最近 10 条，完整流水请前往「积分流水」页面筛选该用户
                </Typography.Text>
              </div>
            )}
          </DrawerSection>

          <DrawerSection icon={HistoryOutlined} title="认证历史">
            {renderAuthTimeline()}
          </DrawerSection>
        </>
      ) : null}
    </Drawer>
  )
}
