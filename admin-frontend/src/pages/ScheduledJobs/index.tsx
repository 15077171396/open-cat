import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Button,
  Card,
  Form,
  InputNumber,
  Popconfirm,
  Segmented,
  Skeleton,
  Switch,
  Tag,
  TimePicker,
  Tooltip,
  Typography,
} from 'antd'
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  CloseCircleFilled,
  InfoCircleFilled,
  EditOutlined,
  ExclamationCircleFilled,
  MinusCircleFilled,
  PlayCircleOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { apiGet, apiPost, apiPut } from '../../api/client'
import { getErrorMessage } from '../../utils/error'
import { toast } from '../../utils/toast'
import { useAuthStore } from '../../store/authStore'
import type { NormalizedScheduledJob, ScheduledJobsData } from '../../types'
import { formatAge, formatCst, normalizeScheduledJobs } from '../../components/dashboard/dashboardUtils'
import ModalForm from '../../components/ui/ModalForm'

/**
 * 定时任务（系统 → 定时任务）。
 *
 * 为什么要有这个页面：定时任务是**最容易静默失效**的一环 —— 任务没跑、跑失败、
 * 或者压根没注册，应用日志里都只是少了一行，没人会发现。数据看板的「系统状态」卡
 * 只能给一句「8 项待处理」，看不出是哪个任务、多久没跑、下次什么时候跑。
 *
 * 能做什么 / 不能做什么（这是刻意的边界）：
 * - 能：**启停**、**改周期**、**立即执行一次**。三者都是「期望状态」，写库后由调度器
 *   每 5 秒对齐一次生效，所以页面必须显示「待生效」，否则用户会以为没保存。
 * - 不能：新增/删除任务。任务体（handler）永远在 `scheduler/tasks.py::JOB_FUNCS` 里，
 *   新增任务本质是一次代码变更 —— 让它在页面上「凭空出现」等于绕过测试与审查。
 *
 * 三条状态口径（改之前先读）：
 * - **保存 ≠ 生效**：`apply.pending` 为真时顶部明确提示；
 * - **调度器没在跑**：`apply.alive` 为假时禁用「立即执行」并说明原因（命令没人消费）；
 * - **只读角色**：没有 `scheduler:manage` 的人看不到操作列（GET 用的是 dashboard:view）。
 */

/** 状态 → 标签颜色（ok 绿 / failed 红 / stale 橙 / never 灰 / disabled 灰） */
const STATUS_COLOR: Record<string, string> = {
  ok: 'success',
  failed: 'error',
  stale: 'warning',
  never: 'default',
  disabled: 'default',
}

type Filter = 'all' | 'bad'

/** 编辑周期的表单值 */
interface ScheduleFormValues {
  schedule_kind: 'daily' | 'interval'
  time?: dayjs.Dayjs
  interval_minutes?: number
}

export default function ScheduledJobsPage() {
  const isSuper = useAuthStore((s) => s.isSuper)
  const permissions = useAuthStore((s) => s.permissions)
  const canManage = isSuper || (permissions?.includes('scheduler:manage') ?? false)

  const [raw, setRaw] = useState<ScheduledJobsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [updatedAt, setUpdatedAt] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  /** 正在提交的任务 id（按钮 loading，防连点） */
  const [busyId, setBusyId] = useState<string | null>(null)
  const [editing, setEditing] = useState<NormalizedScheduledJob | null>(null)
  const [form] = Form.useForm<ScheduleFormValues>()
  /** 弹窗回填值：ModalForm 打开时写入表单（表单里有 useWatch 读 kind，故传外部实例） */
  const [formValues, setFormValues] = useState<Record<string, unknown>>({})
  const scheduleKind = Form.useWatch('schedule_kind', form)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiGet<ScheduledJobsData>('/admin/scheduled-jobs')
      setRaw(data)
      setFailed(false)
      setUpdatedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
    } catch {
      setRaw(null)
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 兜底归一化：缺字段只表现为「—」，不会让整页崩掉
  const data = useMemo(() => normalizeScheduledJobs(raw), [raw])

  const groups = useMemo(
    () =>
      data.groups
        .map((group) => ({
          ...group,
          jobs: filter === 'bad' ? group.jobs.filter((job) => job.status !== 'ok' && job.enabled) : group.jobs,
        }))
        .filter((group) => group.jobs.length > 0),
    [data.groups, filter],
  )

  /** 保存后延迟再拉一次：等调度器对齐（≤5 秒），让「待生效」自己消失 */
  const reloadSoon = useCallback(() => {
    window.setTimeout(() => void load(), 2600)
  }, [load])

  const toggleEnabled = async (job: NormalizedScheduledJob, next: boolean) => {
    setBusyId(job.id)
    try {
      await apiPut(`/admin/scheduled-jobs/${job.id}`, { enabled: next })
      toast.success(next ? '已启用，最多 5 秒后开始按周期执行' : '已停用，最多 5 秒后不再执行')
      await load()
      reloadSoon()
    } catch (err) {
      toast.error(getErrorMessage(err, '保存失败'))
    } finally {
      setBusyId(null)
    }
  }

  const runNow = async (job: NormalizedScheduledJob) => {
    setBusyId(job.id)
    try {
      await apiPost(`/admin/scheduled-jobs/${job.id}/run`)
      toast.success('已下发执行命令，约 1 秒后开始执行；稍后刷新可看结果')
    } catch (err) {
      toast.error(getErrorMessage(err, '下发失败'))
    } finally {
      setBusyId(null)
    }
  }

  const openEditor = (job: NormalizedScheduledJob) => {
    setEditing(job)
    setFormValues({
      schedule_kind: job.config.scheduleKind === 'interval' ? 'interval' : 'daily',
      time: dayjs().hour(job.config.hour).minute(job.config.minute).second(0),
      interval_minutes: job.config.intervalMinutes,
    })
  }

  const saveSchedule = async (raw: Record<string, unknown>) => {
    if (!editing) return
    // ModalForm 传宽类型；字段定义在 ScheduleFormValues，收敛回具体类型
    const values = raw as unknown as ScheduleFormValues
    try {
      const payload =
        values.schedule_kind === 'interval'
          ? { schedule_kind: 'interval', interval_minutes: values.interval_minutes }
          : {
              schedule_kind: 'daily',
              hour: values.time?.hour() ?? 0,
              minute: values.time?.minute() ?? 0,
            }
      await apiPut(`/admin/scheduled-jobs/${editing.id}`, payload)
      toast.success('周期已保存，最多 5 秒后生效')
      setEditing(null)
      await load()
      reloadSoon()
    } catch (err) {
      toast.error(getErrorMessage(err, '保存失败'))
    }
  }

  const schedulerDown = !data.apply.alive

  return (
    <div>
      <div className="dash-header">
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            <ClockCircleOutlined style={{ marginRight: 8, color: '#1677ff' }} />
            定时任务
          </Typography.Title>
          <div className="dash-header__sub">
            <span>{updatedAt ? `更新于 ${updatedAt}` : '正在加载…'}</span>
            <span className="dash-header__dot">·</span>
            <span>周期按{data.timezone} · 心跳保留 {data.heartbeatTtlHours} 小时</span>
            {data.apply.appliedAt ? (
              <>
                <span className="dash-header__dot">·</span>
                <span>调度器最后一次对齐 {formatCst(data.apply.appliedAt)}</span>
              </>
            ) : null}
          </div>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading} disabled={loading}>
          刷新
        </Button>
      </div>

      {failed ? (
        <Banner
          tone="error"
          title="定时任务状态加载失败"
          description="接口不可用或后端未重启（新接口返回 404）。下方不展示任何状态，以免把「读不到」误当成「一切正常」。"
          action={
            <Button size="small" onClick={load}>
              重试
            </Button>
          }
        />
      ) : null}

      {!failed && !data.configsAvailable ? (
        <Banner
          tone="warning"
          title="配置表不可用，当前按代码默认周期运行"
          description={
            <>
              多半是迁移没执行：<code>alembic upgrade head</code>。在此之前启停/改周期都保存不了，
              任务仍按代码里的默认值调度。
            </>
          }
        />
      ) : null}

      {/* 调度器存活是比「配置是否生效」更前置的问题：它挂了，改什么都白改 */}
      {!failed && schedulerDown ? (
        <Banner
          tone="warning"
          title="调度器未运行 —— 配置改了也不会生效，「立即执行」也发不出去"
          description={
            <>
              最近 60 秒没有收到调度器心跳。排查顺序：① 容器是否在跑（<code>docker compose ps scheduler</code>）；
              ② 它是否连到同一个 Redis；③ 首次部署后要等第一个周期才有记录。
              调度器没起来的这段时间里，所有任务都不会执行（页面显示的是最后一次心跳的状态）。
            </>
          }
        />
      ) : null}

      {!failed && !schedulerDown && data.apply.pending ? (
        <Banner
          tone="info"
          title="配置已保存，等待调度器生效（最多 5 秒）"
          description="调度器每 5 秒对齐一次配置。若超过 1 分钟仍是这个状态，说明调度器读不到最新的配置表。"
          action={
            <Button size="small" onClick={load}>
              刷新
            </Button>
          }
        />
      ) : null}

      {/* 全员静默是一条独立结论：所有启用中的任务都没有心跳，几乎只可能是调度器没起 */}
      {!failed && data.allSilent && !schedulerDown ? (
        <Banner
          tone="warning"
          title="启用中的任务都没有执行心跳"
          description="调度器在跑（有心跳），但任务一次都没执行过：可能是首次部署还没到第一个周期，也可能任务注册名写错了（与 JOB_FUNCS 不一致）。"
        />
      ) : null}

      {/* 概览：先给结论，再看明细 */}
      <div className="sys-tiles" style={{ marginBottom: 16 }}>
        <div className="sys-tile sys-tile--unknown">
          <div className="sys-tile__label">任务总数</div>
          <div className="sys-tile__value">{data.summary.total}</div>
          <div className="sys-tile__sub">
            {data.groups.length} 个分组
            {data.summary.disabled ? ` · ${data.summary.disabled} 个已停用` : ''}
          </div>
        </div>
        <div className={`sys-tile sys-tile--${data.summary.ok ? 'ok' : 'unknown'}`}>
          <div className="sys-tile__label">正常运行</div>
          <div className="sys-tile__value">
            <CheckCircleFilled style={{ color: '#52c41a' }} />
            <span style={{ color: '#52c41a' }}>{data.summary.ok}</span>
          </div>
          <div className="sys-tile__sub">上次执行成功且未超期</div>
        </div>
        <div className={`sys-tile sys-tile--${data.summary.failed ? 'fail' : 'ok'}`}>
          <div className="sys-tile__label">上次失败</div>
          <div className="sys-tile__value">
            <ExclamationCircleFilled style={{ color: data.summary.failed ? '#ff4d4f' : '#d9d9d9' }} />
            <span style={{ color: data.summary.failed ? '#ff4d4f' : 'rgba(0,0,0,0.45)' }}>
              {data.summary.failed}
            </span>
          </div>
          <div className="sys-tile__sub">看应用日志里的任务异常堆栈</div>
        </div>
        <div className={`sys-tile sys-tile--${data.summary.stale ? 'warn' : 'ok'}`}>
          <div className="sys-tile__label">未按时执行</div>
          <div className="sys-tile__value">
            <SyncOutlined style={{ color: data.summary.stale ? '#faad14' : '#d9d9d9' }} />
            <span style={{ color: data.summary.stale ? '#faad14' : 'rgba(0,0,0,0.45)' }}>
              {data.summary.stale}
            </span>
          </div>
          <div className="sys-tile__sub">跑过，但超过预期周期没再跑</div>
        </div>
        <div className={`sys-tile sys-tile--${data.summary.never ? 'warn' : 'ok'}`}>
          <div className="sys-tile__label">从未执行</div>
          <div className="sys-tile__value">
            <MinusCircleFilled style={{ color: data.summary.never ? '#faad14' : '#d9d9d9' }} />
            <span style={{ color: data.summary.never ? '#faad14' : 'rgba(0,0,0,0.45)' }}>
              {data.summary.never}
            </span>
          </div>
          <div className="sys-tile__sub">没有心跳记录（含密钥未配置的跳过）</div>
        </div>
      </div>

      <div className="jobs-toolbar">
        <Segmented
          size="small"
          value={filter}
          onChange={(value) => setFilter(value as Filter)}
          options={[
            { label: `全部 ${data.summary.total}`, value: 'all' },
            { label: `仅异常 ${data.summary.attention}`, value: 'bad' },
          ]}
        />
        <span className="jobs-toolbar__hint">
          {canManage
            ? '任务在独立调度容器内执行；改动最多 5 秒后生效'
            : '只读：启停 / 改周期需要「定时任务管理」权限'}
        </span>
      </div>

      {loading && !raw ? (
        <Card className="dash-card" variant="outlined">
          <Skeleton active paragraph={{ rows: 6 }} />
        </Card>
      ) : null}

      {!loading && !failed && groups.length === 0 ? (
        <Card className="dash-card" variant="outlined">
          <div className="sys-empty">
            <CheckCircleFilled style={{ color: '#52c41a', marginRight: 6 }} />
            {filter === 'bad' ? '没有异常任务，全部按周期正常执行' : '没有任何任务'}
          </div>
        </Card>
      ) : null}

      {groups.map((group) => {
        const badCount = group.jobs.filter((job) => job.status !== 'ok' && job.enabled).length
        return (
          <Card
            key={group.key}
            className="dash-card"
            variant="outlined"
            style={{ marginBottom: 16 }}
            title={
              <span style={{ fontSize: 15, fontWeight: 600 }}>
                {group.label}
                <span className="jobs-group__count">{group.jobs.length} 个任务</span>
                {badCount ? (
                  <Tag color="warning" style={{ marginLeft: 8 }}>
                    {badCount} 项异常
                  </Tag>
                ) : null}
              </span>
            }
          >
            <div className={`jobs-table${canManage ? ' jobs-table--manage' : ''}`}>
              <div className="jobs-table__head">
                <span>状态</span>
                <span>任务</span>
                <span>调度周期</span>
                <span>上次执行</span>
                <span>下次执行</span>
                <span>上次结果</span>
                {canManage ? <span>操作</span> : null}
              </div>
              {group.jobs.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  canManage={canManage}
                  busy={busyId === job.id}
                  cooldown={data.runCooldownSeconds}
                  onToggle={(next) => toggleEnabled(job, next)}
                  onRun={() => runNow(job)}
                  onEdit={() => openEditor(job)}
                />
              ))}
            </div>
          </Card>
        )
      })}

      <Card
        className="dash-card"
        variant="outlined"
        title={<span style={{ fontSize: 15, fontWeight: 600 }}>这套任务是怎么跑的</span>}
      >
        <ul className="jobs-notes">
          <li>
            <strong>执行位置</strong>：调度器是独立容器（<code>python -m scheduler.tasks</code>），
            与 API 进程分离。它没起来时接口一切正常，只是任务不再执行 —— 本页就是为这种情况准备的。
          </li>
          <li>
            <strong>启停与改周期怎么生效</strong>：保存的是**期望状态**（写数据库），调度器每 5 秒
            比对一次配置指纹并热重载；改完不需要重启容器。页面顶部会显示「等待生效」直到对齐完成。
          </li>
          <li>
            <strong>立即执行</strong>：命令写进 Redis 队列，由调度器取到后执行一次（约 1 秒）。
            它**不改周期**，也不会打乱下一次调度；同一任务同时只会跑一份（避免清理类任务互相踩）。
          </li>
          <li>
            <strong>心跳机制</strong>：每次执行结束往 Redis 写 <code>ops:job:&lt;id&gt;</code>
            （保留 {data.heartbeatTtlHours} 小时，<strong>不是审计留痕</strong>）。任务内部刻意吞异常
            以免影响下次调度，失败靠返回值判定，所以「上次失败」一定是真失败。
          </li>
          <li>
            <strong>不能在这里新增任务</strong>：任务体写在 <code>scheduler/tasks.py</code> 的
            <code>JOB_FUNCS</code>，并在 <code>services/scheduled_job_service.py</code> 的
            <code>JOB_SPECS</code> 里声明周期与说明（两侧由单测双向比对）。新增一种任务 = 一次代码变更，
            这样才有测试、审查与回滚。
          </li>
        </ul>
      </Card>

      {/* 周期编辑：只改周期，不动启停（启停用行内开关，一键完成） */}
      <ModalForm
        open={editing !== null}
        title={`调整执行周期${editing ? `：${editing.label}` : ''}`}
        onCancel={() => setEditing(null)}
        onSubmit={saveSchedule}
        okText="保存"
        form={form}
        initialValues={formValues}
        formStyle={{ marginTop: 12 }}
      >
          <Form.Item name="schedule_kind" label="调度方式">
            <Segmented
              options={[
                { label: '每天固定时刻', value: 'daily' },
                { label: '每隔 N 分钟', value: 'interval' },
              ]}
            />
          </Form.Item>
          {scheduleKind === 'interval' ? (
            <Form.Item
              name="interval_minutes"
              label="间隔（分钟）"
              extra="1 - 10080（7 天）。每分钟任务开销很小；遍历型任务别设得太密。"
              rules={[{ required: true, message: '请输入间隔分钟数' }]}
            >
              <InputNumber min={1} max={10080} style={{ width: 200 }} />
            </Form.Item>
          ) : (
            <Form.Item
              name="time"
              label="每天执行时刻（北京时间）"
              extra="建议错开整点：整点是其它任务与备份的集中时段，错开能避免同时抢 DB。"
              rules={[{ required: true, message: '请选择执行时刻' }]}
            >
              <TimePicker format="HH:mm" minuteStep={5} style={{ width: 200 }} needConfirm={false} />
            </Form.Item>
          )}
          {editing ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              当前：{editing.schedule}
              {editing.customized ? `（代码默认 ${editing.defaultSchedule}）` : '（代码默认值）'}
            </Typography.Text>
          ) : null}
      </ModalForm>
    </div>
  )
}

/**
 * 页面横幅：**白底 + 左侧色条**，刻意不用 antd `Alert` 的整块黄/蓝底。
 *
 * 理由：浅色后台里大面积黄底会整片发闷，四条横幅连排时整页泛黄（截图反馈「看着怪怪的」）；
 * 严重程度用「图标颜色 + 左侧 3px 色条」表达已经足够，也更容易和卡片质感统一。
 */
function Banner({
  tone,
  title,
  description,
  action,
}: {
  tone: 'info' | 'warning' | 'error'
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  const color = tone === 'error' ? '#ff4d4f' : tone === 'warning' ? '#faad14' : '#1677ff'
  const Icon = tone === 'error' ? CloseCircleFilled : tone === 'warning' ? ExclamationCircleFilled : InfoCircleFilled
  return (
    <div className="jobs-banner" style={{ borderLeftColor: color }}>
      <Icon style={{ color, marginTop: 3, flex: 'none' }} />
      <div className="jobs-banner__body">
        <div className="jobs-banner__title">{title}</div>
        {description ? <div className="jobs-banner__desc">{description}</div> : null}
      </div>
      {action ? <div className="jobs-banner__action">{action}</div> : null}
    </div>
  )
}

/** 单行任务：宽屏是表格行，窄屏标签展开成「键值」形式（见 custom.css 的 .jobs-cell__k） */
function JobRow({
  job,
  canManage,
  busy,
  cooldown,
  onToggle,
  onRun,
  onEdit,
}: {
  job: NormalizedScheduledJob
  canManage: boolean
  busy: boolean
  cooldown: number
  onToggle: (next: boolean) => void
  onRun: () => void
  onEdit: () => void
}) {
  const healthy = job.status === 'ok'
  const unconfigured = job.requires.filter((item) => !item.configured)

  // 「上次结果」：优先展示任务自己返回的产出摘要，其次超期原因，最后给一句状态兜底
  const resultText = job.detail || job.staleReason

  return (
    <div className={`jobs-row${job.enabled ? '' : ' jobs-row--disabled'}`}>
      <div className="jobs-cell">
        <Tag color={STATUS_COLOR[job.status] ?? 'default'} style={{ marginInlineEnd: 0 }}>
          {job.statusText}
        </Tag>
      </div>

      <div className="jobs-cell">
        <span className="jobs-cell__k">任务</span>
        <div className="jobs-name">{job.label}</div>
        {job.desc ? <div className="jobs-desc">{job.desc}</div> : null}
        {job.note ? <div className="jobs-desc jobs-desc--note">{job.note}</div> : null}
        {unconfigured.length ? (
          <div className="jobs-desc jobs-desc--warn">
            <ExclamationCircleFilled style={{ marginRight: 4 }} />
            未配置 {unconfigured.map((item) => item.label).join(' / ')}，任务会跳过（不算失败）
          </div>
        ) : null}
      </div>

      <div className="jobs-cell jobs-cell--strong">
        <span className="jobs-cell__k">调度周期</span>
        {job.schedule}
        {job.customized ? (
          <Tooltip title={`代码默认值：${job.defaultSchedule}`}>
            <Tag color="blue" style={{ marginLeft: 6 }}>
              已调整
            </Tag>
          </Tooltip>
        ) : null}
        {job.scheduleKind === 'daily' ? <span className="jobs-cell__sub">（北京时间）</span> : null}
      </div>

      <div className="jobs-cell">
        <span className="jobs-cell__k">上次执行</span>
        {job.lastRunAt ? (
          <Tooltip title={formatCst(job.lastRunAt)}>
            <span>{formatAge(job.lastAgeSeconds)}</span>
          </Tooltip>
        ) : (
          <span style={{ color: '#bfbfbf' }}>无记录</span>
        )}
        <div className="jobs-cell__sub">{formatCst(job.lastRunAt)}</div>
      </div>

      <div className="jobs-cell">
        <span className="jobs-cell__k">下次执行</span>
        {job.enabled ? (
          <>
            <span style={{ color: healthy ? undefined : '#8c8c8c' }}>{formatCst(job.nextRunAt)}</span>
            <div className="jobs-cell__sub">预计</div>
          </>
        ) : (
          <span style={{ color: '#bfbfbf' }}>已停用</span>
        )}
      </div>

      <div className="jobs-cell">
        <span className="jobs-cell__k">上次结果</span>
        {resultText ? (
          <Tooltip title={resultText}>
            <span className="jobs-ellipsis">{resultText}</span>
          </Tooltip>
        ) : (
          <span style={{ color: '#bfbfbf' }}>—</span>
        )}
      </div>

      {canManage ? (
        <div className="jobs-cell jobs-cell--actions">
          <span className="jobs-cell__k">操作</span>
          <Tooltip title={job.enabled ? '停用后不再按周期执行' : '启用后恢复按周期执行'}>
            <Switch
              size="small"
              checked={job.enabled}
              loading={busy}
              onChange={onToggle}
              aria-label={job.enabled ? '停用任务' : '启用任务'}
            />
          </Tooltip>
          <Tooltip title={`立即执行一次（不改周期）· 触发后 ${cooldown} 秒内不可重复`}>
            <Popconfirm
              title="立即执行一次？"
              description="会在调度器容器内真实执行（可能改动数据），不改周期。"
              okText="执行"
              cancelText="取消"
              onConfirm={onRun}
              disabled={busy}
            >
              <Button type="text" size="small" icon={<PlayCircleOutlined />} disabled={busy} aria-label="立即执行" />
            </Popconfirm>
          </Tooltip>
          <Tooltip title="调整执行周期">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={onEdit}
              disabled={busy}
              aria-label="调整周期"
            />
          </Tooltip>
        </div>
      ) : null}
    </div>
  )
}
