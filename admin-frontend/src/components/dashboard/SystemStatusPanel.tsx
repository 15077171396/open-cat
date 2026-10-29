import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Card, Tag, Tooltip, Typography } from 'antd'
import {
  CheckCircleFilled,
  ExclamationCircleFilled,
  MinusCircleFilled,
  RightOutlined,
} from '@ant-design/icons'
import type { NormalizedSystemStatus } from '../../types'
import { formatAge } from './dashboardUtils'

/**
 * 系统状态卡：把「要 SSH 上去看」的运维信息放到看板上。
 *
 * 设计取舍（改之前先读）：
 * - **未知 ≠ 失败**：探针读不到版本/时延显示灰色「未知」，不是红色。
 *   把未知画成红色会让真正的故障淹没在噪声里。
 * - **只显示需要人动手的**：`warnings` 由后端汇总（含「三个空配置」这类
 *   不配也不报错的漏配项），前端不再二次判断业务规则，避免两处口径漂移。
 * - **失败态必须可见**：接口挂了时 `data` 全是 0/空，与「一切正常」长得一样，
 *   所以失败时显式画一条提示条（与 AI 用量卡同一处理方式）。
 *
 * 2026-10-27 重排（原来 10 条一模一样的黄色提示 + 一张扁平的事实表，看的人不知道从哪下手）：
 * 现在是三层 ——
 * ① 「健康摘要」五个格子回答「现在有没有事」；
 * ② 「待处理」按后端的 category 分档筛选，回答「我该先动哪一条」；
 * ③ 「运行细节」收拢日志/环境/通道配置，回答「要查的时候去哪看」。
 * 分类口径完全来自后端（`system_status_service._collect_warnings`），前端不按关键词猜。
 *
 * 定时任务只留一行概览 + 跳转：明细已经独立成「系统 → 定时任务」页，
 * 8 个任务逐个铺开正是这张卡以前最占地方、也最容易看花的部分。
 */
interface Props {
  data: NormalizedSystemStatus
  loading?: boolean
  failed?: boolean
  onRefresh?: () => void
  /** 跳转「定时任务」页；不传则不显示入口（如权限不足） */
  onOpenJobs?: () => void
}

const COLORS = {
  ok: '#52c41a',
  fail: '#ff4d4f',
  unknown: '#8c8c8c',
  warn: '#faad14',
} as const

const titleStyle = { fontSize: 15, fontWeight: 600 } as const

/** 三态探针 → 文案 + 颜色 + 色调（ok=null 走 unknown 分支） */
function probeState(ok: boolean | null, ms: number): { text: string; tone: Tone; icon: ReactNode } {
  if (ok === null) {
    return { text: '未知', tone: 'unknown', icon: <MinusCircleFilled style={{ color: COLORS.unknown }} /> }
  }
  if (!ok) {
    return { text: '异常', tone: 'fail', icon: <ExclamationCircleFilled style={{ color: COLORS.fail }} /> }
  }
  return {
    text: ms > 0 ? `正常 ${ms}ms` : '正常',
    tone: 'ok',
    icon: <CheckCircleFilled style={{ color: COLORS.ok }} />,
  }
}

/** 摘要格子的四种色调：左描边 + 数值色，一处定义避免五处各写一份 */
type Tone = 'ok' | 'warn' | 'fail' | 'unknown'

const TONE_COLOR: Record<Tone, string> = {
  ok: COLORS.ok,
  warn: COLORS.warn,
  fail: COLORS.fail,
  unknown: COLORS.unknown,
}

/** 健康摘要格：左边一条色带 + 标签 + 主值 + 一行副值 */
function Tile({
  label,
  value,
  sub,
  tone,
  icon,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone: Tone
  icon?: ReactNode
}) {
  return (
    <div className={`sys-tile sys-tile--${tone}`}>
      <div className="sys-tile__label">{label}</div>
      <div className="sys-tile__value">
        {icon}
        <span>{value}</span>
      </div>
      {sub ? <div className="sys-tile__sub">{sub}</div> : null}
    </div>
  )
}

/** 运行细节里的一行「标签：值」 */
function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="sys-detail">
      <span className="sys-detail__label">{label}</span>
      <span className="sys-detail__value">{children}</span>
    </div>
  )
}

function ChannelTag({ name, configured }: { name: string; configured: boolean }) {
  return (
    <Tag color={configured ? 'success' : 'default'} style={{ marginInlineEnd: 6 }}>
      {name} {configured ? '已配' : '未配'}
    </Tag>
  )
}

export default function SystemStatusPanel({ data, loading, failed, onRefresh, onOpenJobs }: Props) {
  const [activeCategory, setActiveCategory] = useState<string>('all')

  const mysql = probeState(data.mysql.ok, data.mysql.ms)
  const redis = probeState(data.redis.ok, data.redis.ms)

  // 依赖结论取两者中最差的一个：任一异常就整体标异常，任一未知就至少标未知
  const dependencyTone: Tone =
    data.mysql.ok === false || data.redis.ok === false
      ? 'fail'
      : data.mysql.ok === null || data.redis.ok === null
        ? 'unknown'
        : 'ok'
  const dependencyText =
    dependencyTone === 'fail' ? '有依赖异常' : dependencyTone === 'unknown' ? '部分未知' : '全部正常'

  const schemaTone: Tone =
    data.schema.in_sync === null ? 'unknown' : data.schema.in_sync ? 'ok' : 'fail'
  const schemaText =
    data.schema.in_sync === null ? '未知' : data.schema.in_sync ? '一致' : '不一致'

  // 任务口径与「定时任务」页一致：stale 或 ok===false 算不正常（含「从未执行」），
  // 但**已停用的不算**——那是人主动关的，天天挂在看板上只会让真故障被淹没
  const jobEnabled = data.jobs.filter((job) => job.enabled)
  const jobDisabled = data.jobs.length - jobEnabled.length
  const jobBad = jobEnabled.filter((job) => job.stale || job.ok === false).length
  const jobTone: Tone = jobEnabled.length === 0 ? 'unknown' : jobBad === 0 ? 'ok' : 'warn'

  const backupTone: Tone = !data.backup.visible ? 'unknown' : data.backup.stale ? 'fail' : 'ok'
  const backupText = !data.backup.visible
    ? '不可见'
    : data.backup.age_hours === null
      ? '无备份文件'
      : formatAge(data.backup.age_hours * 3600)

  const qualityTone: Tone = !data.dataQuality.available
    ? 'unknown'
    : data.dataQuality.issue_total > 0
      ? 'warn'
      : 'ok'

  // 待处理项按后端下发的 category 分档（顺序即「故障优先」的优先级）
  const categories = useMemo(() => {
    const order = ['dependency', 'backup', 'config', 'job', 'data', 'other']
    const map = new Map<string, { key: string; label: string; count: number; hasError: boolean }>()
    for (const item of data.warnings) {
      const key = item.category || 'other'
      const entry = map.get(key) ?? {
        key,
        label: item.category_label || '其它',
        count: 0,
        hasError: false,
      }
      entry.count += 1
      entry.hasError = entry.hasError || item.level === 'error'
      map.set(key, entry)
    }
    return [...map.values()].sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key))
  }, [data.warnings])

  const errorCount = data.warnings.filter((item) => item.level === 'error').length
  const effectiveCategory =
    activeCategory === 'all' || categories.some((c) => c.key === activeCategory)
      ? activeCategory
      : 'all'
  const visibleWarnings =
    effectiveCategory === 'all'
      ? data.warnings
      : data.warnings.filter((item) => (item.category || 'other') === effectiveCategory)

  // 分档筛选之后仍要回答「然后呢」：配置类项在环境变量里补齐，任务类项去「定时任务」页看
  const visibleCategories = new Set(visibleWarnings.map((item) => item.category || 'other'))
  const showConfigHint = visibleCategories.has('config')
  const showJobHint = visibleCategories.has('job')

  return (
    <Card
      className="dash-card"
      variant="outlined"
      title={
        <span style={titleStyle}>
          系统状态
          {data.env.app_env ? (
            <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
              {data.env.app_env}
              {data.env.release ? ` · ${data.env.release}` : ''}
            </Typography.Text>
          ) : null}
        </span>
      }
      extra={
        failed ? (
          <Tag color="error">数据异常</Tag>
        ) : loading ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            加载中…
          </Typography.Text>
        ) : errorCount ? (
          <Tag color="error">{errorCount} 项故障</Tag>
        ) : data.warnings.length ? (
          <Tag color="warning">{data.warnings.length} 项待处理</Tag>
        ) : (
          <Tag color="success">全部正常</Tag>
        )
      }
    >
      {failed ? (
        <div className="sys-banner sys-banner--fail">
          状态接口不可用，下方数据可能为 0 或过期 —— 这本身可能是服务异常的信号。
          {onRefresh ? (
            <Typography.Link style={{ marginLeft: 8 }} onClick={onRefresh}>
              重试
            </Typography.Link>
          ) : null}
        </div>
      ) : null}

      {/* ① 健康摘要：先回答「现在有没有事」，五行五格，异常才变红/黄 */}
      <div className="sys-tiles">
        <Tile
          label="依赖服务"
          tone={dependencyTone}
          icon={<span style={{ color: TONE_COLOR[dependencyTone] }}>{mysql.icon}</span>}
          value={<span style={{ color: TONE_COLOR[dependencyTone] }}>{dependencyText}</span>}
          sub={
            <>
              MySQL {data.mysql.error ? <span style={{ color: COLORS.fail }}>{data.mysql.error}</span> : mysql.text}
              {' · '}
              Redis {data.redis.error ? <span style={{ color: COLORS.fail }}>{data.redis.error}</span> : redis.text}
            </>
          }
        />
        <Tile
          label="数据库版本"
          tone={schemaTone}
          value={<span style={{ color: TONE_COLOR[schemaTone] }}>{schemaText}</span>}
          sub={
            data.schema.db_revision || data.schema.code_head
              ? `库 ${data.schema.db_revision || '?'} / 代码 ${data.schema.code_head || '?'}`
              : '未能读取迁移版本'
          }
        />
        <Tile
          label="定时任务"
          tone={jobTone}
          value={
            jobEnabled.length === 0 ? (
              <span style={{ color: COLORS.unknown }}>全部停用</span>
            ) : (
              <>
                <span style={{ color: jobBad ? COLORS.warn : COLORS.ok }}>
                  {jobEnabled.length - jobBad}
                </span>
                <span style={{ color: '#8c8c8c', fontSize: 13, fontWeight: 400 }}>
                  {' '}
                  / {jobEnabled.length} 正常
                </span>
              </>
            )
          }
          sub={
            jobBad
              ? `${jobBad} 个任务未按时执行或失败`
              : jobDisabled
                ? `另有 ${jobDisabled} 个已停用`
                : jobEnabled.length
                  ? '全部按周期执行'
                  : '未获取到任务清单'
          }
        />
        <Tile
          label="最近备份"
          tone={backupTone}
          value={<span style={{ color: TONE_COLOR[backupTone] }}>{backupText}</span>}
          sub={
            !data.backup.visible
              ? '备份目录未挂载'
              : data.backup.latest || '目录可见但还没有 .sql.gz'
          }
        />
        <Tile
          label="数据巡检"
          tone={qualityTone}
          value={
            !data.dataQuality.available ? (
              <span style={{ color: COLORS.unknown }}>暂无结果</span>
            ) : data.dataQuality.issue_total > 0 ? (
              <Tooltip title={data.dataQuality.issues.map((i) => `${i.text} ${i.count} 条`).join('；')}>
                <span style={{ color: COLORS.warn }}>{data.dataQuality.issue_total} 条异常</span>
              </Tooltip>
            ) : (
              <span style={{ color: COLORS.ok }}>无异常</span>
            )
          }
          sub={data.dataQuality.available ? '每日 05:00 巡检' : '巡检结果已过期或尚未执行'}
        />
      </div>

      {/* ② 待处理：按类别分档，避免「10 条一样的黄字」糊成一片 */}
      <div className="sys-section">
        <div className="sys-section__head">
          <span className="sys-section__title">
            待处理事项
            {data.warnings.length ? <span className="sys-section__count">{data.warnings.length}</span> : null}
          </span>
          {data.warnings.length ? (
            <div className="sys-filters">
              <button
                type="button"
                className={`sys-chip${effectiveCategory === 'all' ? ' sys-chip--active' : ''}`}
                onClick={() => setActiveCategory('all')}
              >
                全部 {data.warnings.length}
              </button>
              {categories.map((category) => (
                <button
                  key={category.key}
                  type="button"
                  className={`sys-chip${category.hasError ? ' sys-chip--error' : ''}${
                    effectiveCategory === category.key ? ' sys-chip--active' : ''
                  }`}
                  onClick={() => setActiveCategory(category.key)}
                >
                  {category.label} {category.count}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        {data.warnings.length === 0 ? (
          <div className="sys-empty">
            <CheckCircleFilled style={{ color: COLORS.ok, marginRight: 6 }} />
            依赖、备份、定时任务与数据巡检都没有需要处理的项
          </div>
        ) : (
          <div className="sys-warn-list">
            {visibleWarnings.map((item, index) => (
              <div key={`${item.category}-${index}`} className="sys-warn">
                <ExclamationCircleFilled
                  style={{
                    color: item.level === 'error' ? COLORS.fail : COLORS.warn,
                    marginTop: 4,
                    flex: 'none',
                  }}
                />
                <span className="sys-warn__text">{item.text}</span>
                <span className="sys-warn__tag">{item.category_label || '其它'}</span>
              </div>
            ))}
          </div>
        )}

        {showConfigHint || showJobHint ? (
          <div className="sys-footnote">
            {showConfigHint ? (
              <span>
                配置类项在部署环境变量（<code>.env</code> / compose）里补齐，改完需重启后端才生效，不配只是静默失效。
              </span>
            ) : null}
            {showJobHint ? (
              <span>
                定时任务类项{onOpenJobs ? '请到「定时任务」页看每个任务的周期与上次结果' : '需在调度容器内排查'}。
              </span>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* ③ 运行细节：平时不用看，排查时要知道去哪看 */}
      <div className="sys-section">
        <div className="sys-section__head">
          <span className="sys-section__title">运行细节</span>
          {onOpenJobs ? (
            <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={onOpenJobs}>
              定时任务明细 <RightOutlined style={{ fontSize: 10 }} />
            </Button>
          ) : null}
        </div>
        <div className="sys-details">
          <DetailRow label="运行环境">
            {data.env.app_env || '未知'}
            {data.env.release ? ` · ${data.env.release}` : ''}
            {data.env.monitoring_enabled ? ' · 错误上报已启用' : ' · 错误上报未启用'}
          </DetailRow>
          <DetailRow label="日志目录">
            {data.logs.visible ? (
              <>
                <code>{data.logs.dir}</code>
                <span style={{ marginLeft: 8, color: data.logs.writable ? undefined : COLORS.fail }}>
                  {data.logs.writable ? '可写' : '不可写'} ·{' '}
                  {data.logs.last_write_age_minutes === null
                    ? '暂无写入'
                    : `最后写入 ${formatAge(data.logs.last_write_age_minutes * 60)}`}
                </span>
              </>
            ) : (
              <span style={{ color: COLORS.unknown }}>不可见（目录未挂载）</span>
            )}
          </DetailRow>
          <DetailRow label="备份目录">
            {data.backup.visible ? <code>{data.backup.dir}</code> : <span style={{ color: COLORS.unknown }}>不可见</span>}
          </DetailRow>
          <DetailRow label="告警通道">
            <ChannelTag name="错误上报" configured={data.alerts.sentry} />
            <ChannelTag name="看门狗" configured={data.alerts.watchdog_webhook} />
            <span className="sys-hint">未配置不报错，只是静默不生效</span>
          </DetailRow>
          <DetailRow label="收录推送">
            <ChannelTag name="百度" configured={data.alerts.baidu_push} />
            <ChannelTag name="IndexNow" configured={data.alerts.indexnow} />
          </DetailRow>
        </div>
      </div>
    </Card>
  )
}
