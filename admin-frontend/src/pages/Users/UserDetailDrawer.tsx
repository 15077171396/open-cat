import { Avatar, Button, Drawer, Descriptions, Spin, Space, Tag, Typography } from 'antd'
import { EditOutlined, InfoCircleOutlined, UserOutlined } from '@ant-design/icons'
import { formatDateTime } from '../../utils/datetime'
import type { User } from '../../types'
import { ROLE_META } from './roleMeta'

interface UserDetailDrawerProps {
  open: boolean
  loading: boolean
  user: User | null
  onClose: () => void
  onEdit: () => void
}

/** 分区块标题（与后台编辑页的 `.adm-section` 同一套视觉） */
function DrawerSection({
  icon,
  title,
  children,
}: {
  icon: React.ComponentType<{ style?: React.CSSProperties }>
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

/** 用户详情抽屉：基本信息 + RBAC 角色归属 */
export default function UserDetailDrawer({
  open,
  loading,
  user,
  onClose,
  onEdit,
}: UserDetailDrawerProps) {
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
          {/* 头部身份区：头像 + 账号 + 角色/部门角标 */}
          <div className="adm-cell" style={{ marginBottom: 24 }}>
            <Avatar className="adm-avatar" size={64} icon={<UserOutlined />} src={user.avatar_url || undefined} />
            <div className="adm-cell__body">
              <div style={{ fontSize: 17, fontWeight: 600, color: '#1f1f1f', lineHeight: 1.3 }}>
                {user.account}
              </div>
              <Space style={{ marginTop: 6 }} size={6} wrap>
                <Tag color={roleMeta?.color}>{roleMeta?.text}</Tag>
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
              {user.avatar_url && (
                <Descriptions.Item label="头像 URL">
                  <Typography.Text style={{ wordBreak: 'break-all' }} type="secondary">
                    {user.avatar_url}
                  </Typography.Text>
                </Descriptions.Item>
              )}
            </Descriptions>
          </DrawerSection>

          <DrawerSection icon={InfoCircleOutlined} title="后台角色与部门">
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="后台角色">
                {user.rbac_roles?.length ? (
                  <Space size={4} wrap>
                    {user.rbac_roles.map((r) => (
                      <Tag key={r.id} color="purple" style={{ margin: 0 }}>
                        {r.name}
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  <Typography.Text type="secondary">未分配</Typography.Text>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="部门">
                {user.department_name ? (
                  <Typography.Text>{user.department_name}</Typography.Text>
                ) : (
                  <Typography.Text type="secondary">未分配</Typography.Text>
                )}
              </Descriptions.Item>
            </Descriptions>
          </DrawerSection>
        </>
      ) : null}
    </Drawer>
  )
}
