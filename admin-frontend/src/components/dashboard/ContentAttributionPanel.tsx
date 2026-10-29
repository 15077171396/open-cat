import { Card, Tag, Tooltip, Typography } from 'antd'
import { AimOutlined } from '@ant-design/icons'
import type { NormalizedAttribution } from '../../types'
import { formatPercent } from './dashboardUtils'

/**
 * 内容归因榜：看过 → 7 天内提交认证。
 *
 * 与「转化漏斗」互补：漏斗告诉你**哪一段在流失**，这张卡告诉你**哪篇内容真的带来了付费意愿**。
 *
 * 三条显示约定：
 * - `available === false`（还没采集 / 迁移未执行）必须与「采集了但没数据」区分开；
 * - `rate === null`（没有人看过）显示「—」，不能兜底成 0%；
 * - 榜单里同时给「看过人数」与「转化人数」—— 只看转化率会把「1 人看过 1 人转化」
 *   排到「100 人看过 20 人转化」前面，那是噪声不是信号。
 */
interface Props {
  data: NormalizedAttribution
  loading?: boolean
  failed?: boolean
  onRefresh?: () => void
}

const titleStyle = { fontSize: 15, fontWeight: 600 } as const

const TYPE_LABELS: Record<string, string> = {
  tutorial: '教程',
  resource: '资源',
  node: '百科',
  plugin: '扩展',
}

export default function ContentAttributionPanel({ data, loading, failed, onRefresh }: Props) {
  return (
    <Card
      className="dash-card"
      variant="outlined"
      title={
        <span style={titleStyle}>
          <AimOutlined style={{ marginRight: 8, color: '#8c8c8c' }} />
          内容归因
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
            看过 → {data.windowDays} 天内申请认证
          </Typography.Text>
        </span>
      }
      extra={
        failed ? (
          <Tag color="error">数据异常</Tag>
        ) : loading ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            加载中…
          </Typography.Text>
        ) : (
          <Tooltip title="只统计登录用户的浏览（按天去重）；游客没有稳定标识，无法归因">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              近 {data.days} 天 · {data.totalViews} 次浏览 / {data.totalViewers} 人
            </Typography.Text>
          </Tooltip>
        )
      }
    >
      {failed ? (
        <div
          style={{
            background: '#fff2f0',
            border: '1px solid #ffccc7',
            borderRadius: 6,
            padding: '8px 12px',
            fontSize: 13,
            color: '#a8071a',
            marginBottom: 12,
          }}
        >
          归因数据加载失败。
          {onRefresh ? (
            <Typography.Link style={{ marginLeft: 8 }} onClick={onRefresh}>
              重试
            </Typography.Link>
          ) : null}
        </div>
      ) : null}

      {!data.available ? (
        <div style={{ fontSize: 13, color: '#8c8c8c' }}>
          尚未采集到归因数据。若这是刚部署的新版本，请先执行数据库迁移（`alembic upgrade head`）
          并确认后端已重启 —— 之后登录用户每次打开内容详情页都会记录一条（按天去重）。
        </div>
      ) : data.items.length === 0 ? (
        <div style={{ fontSize: 13, color: '#8c8c8c' }}>
          近 {data.days} 天还没有登录用户的内容浏览记录。归因只统计登录用户（游客没有稳定标识）。
        </div>
      ) : (
        <div>
          {data.items.map((item, index) => (
            <div
              key={`${item.type}-${item.id}`}
              style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}
            >
              <span style={{ width: 18, color: '#bfbfbf', fontSize: 12, textAlign: 'right' }}>
                {index + 1}
              </span>
              <Tag color={index < 3 ? 'blue' : 'default'} style={{ marginInlineEnd: 0 }}>
                {TYPE_LABELS[item.type] ?? item.type}
              </Tag>
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  fontSize: 13,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={item.title}
              >
                {item.title || `#${item.id}`}
              </span>
              <span style={{ width: 118, flex: '0 0 auto', textAlign: 'right', fontSize: 12, color: '#8c8c8c' }}>
                看 {item.viewers} / 转化 {item.converted}
              </span>
              <span style={{ width: 52, flex: '0 0 auto', textAlign: 'right', fontSize: 13, fontWeight: 600 }}>
                {formatPercent(item.rate)}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
