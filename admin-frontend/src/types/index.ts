/** 通用分页响应 */
export interface PaginatedResponse<T> {
  items: T[]
  total?: number
  page?: number
  page_size?: number
}

/** 用户角色 */
export type UserRole = 'user' | 'vip' | 'admin'

/** 教程更新状态 */
export type TutorialStatus = 'updating' | 'completed'

/** 积分流水类型：consume=工具消耗 / admin_set=管理员设置 / admin_grant=管理员充值 / admin_deduct=管理员扣减 / redeem=兑换码充值 / invite=邀请奖励 */
export type PointTxType = 'consume' | 'admin_set' | 'admin_grant' | 'admin_deduct' | 'redeem' | 'invite'

/** 星球认证状态 */
type AuthStatus = 'approved' | 'pending' | 'rejected'

/** 用户反馈处理状态（与后端 FEEDBACK_STATUSES 对应） */
export type FeedbackStatus = 'pending' | 'processing' | 'resolved' | 'closed'

/** 管理员用户信息 */
export interface AdminUser {
  id: number
  account: string
  role: UserRole
  avatar_url: string | null
  created_at: string | null
}

/** 普通用户（管理后台视角） */
export interface User {
  id: number
  account: string
  role: UserRole
  avatar_url: string | null
  created_at: string | null
  vip_expire_time: string | null
  /** 积分余额（运行积分工具时消耗） */
  points?: number
  /** 按自然时间倒计时计算的已用时长（封顶 3 小时）；VIP/admin 为实际学习统计 */
  free_used_seconds: number
  /** 剩余免费时长；VIP/admin 不限时为 null */
  free_remaining_seconds: number | null
  auth_status: AuthStatus | null
  star_code: string | null
  pending_auth_code: string | null
  auth_history?: AuthHistoryItem[]
  /** RBAC：管理后台角色（决定后台菜单与操作权限） */
  rbac_roles?: { id: number; code: string; name: string }[]
  /** RBAC：部门归属 */
  department_id?: number | null
  department_name?: string | null
  /** 软删除时间：非空 = 该账号已注销/被删除（account_status=deleted|all 时才有值） */
  deleted_at?: string | null
}

/** 积分流水（管理后台视角，全站流水额外含 user_account） */
export interface PointTransaction {
  id: number
  user_id: number
  /** 全站流水接口返回的用户账号 */
  user_account?: string
  /** 变动值：正=获得，负=消耗 */
  points: number
  /** 变动后余额 */
  balance_after: number
  /** 类型（枚举见 PointTxType） */
  type: PointTxType
  description: string | null
  operator_id: number | null
  /** 全站流水接口返回的操作人账号（outer join 解析，系统写入或账号已删时为 null） */
  operator_account?: string | null
  created_at: string | null
}

/** 积分兑换码 */
export interface RedemptionCode {
  id: number
  code: string
  /** 每份兑换积分 */
  points: number
  /** 最大兑换人数（=1 单人码 / >1 多人限量码） */
  max_uses: number
  /** 已兑换人数 */
  used_count: number
  /** 数据库状态：active=启用 / disabled=已作废 */
  status: 'active' | 'disabled'
  /** 服务端计算的展示状态：active=进行中 / exhausted=已兑完 / expired=已过期 / disabled=已作废 */
  eff_status: 'active' | 'exhausted' | 'expired' | 'disabled'
  /** 是否单人码（max_uses === 1） */
  is_single: boolean
  /** 过期时间（null=永不过期） */
  expires_at: string | null
  note: string | null
  created_at: string | null
}

/** 兑换记录 */
export interface RedemptionRecordItem {
  id: number
  code_id: number
  code: string
  user_id: number
  /** 兑换用户账号 */
  user_account?: string
  points: number
  created_at: string | null
}

/** 认证历史记录 */
export interface AuthHistoryItem {
  status: AuthStatus
  star_code: string
  vip_duration: string | null
  created_at: string | null
  reviewed_at: string | null
  reject_reason: string | null
}

/** 认证申请 */
/** 认证申请（知识星球认证）；审核字段在 pending 时为空 */
export interface AuthRequest {
  id: number
  user_id: number
  account: string | null
  star_code: string
  /** pending / approved / rejected（中文名见 status_label） */
  status: string
  status_label?: string
  vip_duration: string | null
  vip_duration_label?: string | null
  reviewed_by: number | null
  /** 审核人账号（outer join 解析，账号已删时为 null，前端回落 #id） */
  reviewer_account?: string | null
  reviewed_at: string | null
  reject_reason: string | null
  created_at: string
}

/** 网盘链接 */
export interface NetdiskLink {
  platform: string
  url: string
  extract_code: string
}

/** 扩展来源 */
export interface PluginSource {
  source_name: string
  url: string
  extract_code: string
}

/** 教程 */
export interface Tutorial {
  id: number
  title: string
  description: string
  introduction?: string | null
  category: string
  status: TutorialStatus
  content: string
  cover_image: string
  is_published: boolean
  /** 阅读权限（均需登录）：free=免费阅读 / vip=会员专享 */
  access_mode: 'free' | 'vip'
  /** 会员教程积分购买价：>0 时非会员可积分购买后永久阅读 */
  points_price: number
  /** 置顶：true 时前台教程列表最优先展示 */
  is_pinned: boolean
  /** 推荐：true 时前台卡片显示「推荐」角标且排序提前 */
  is_recommended: boolean
  /** SEO 信息：仅输出到教程页 meta 标签供搜索引擎抓取，前台页面不展示 */
  seo_title?: string | null
  seo_description?: string | null
  seo_keywords?: string | null
  /** 学习次数（前台教程卡片展示；后台列表指标列） */
  view_count?: number
  /** 最近更新时间（后台列表展示；前台不用） */
  updated_at?: string | null
  published_at: string | null
  netdisk_links?: NetdiskLink[]
}

/** 资源 */
export interface Resource {
  id: number
  name: string
  description: string
  content: string
  resource_type: string
  file_size: string
  download_count: number
  /** 精选推荐：true 时前台资源页优先展示 */
  is_featured: boolean
  /** 置顶：true 时前台资源列表最优先展示 */
  is_pinned: boolean
  /** 封面图 URL：前台卡片封面与详情页 Hero 背景 */
  cover_image?: string | null
  /** 预览量：前台资源卡片左侧展示的就是它（与扩展卡片同一形态） */
  view_count?: number
  /** 发布作者：前台展示；null = 未填写 */
  author_name?: string | null
  /** 作者头像 URL：null/空 = 前台用作者名首字兜底 */
  author_avatar_url?: string | null
  /** 资源标签（JSON 数组，后台灵活添加） */
  tags?: string[]
  /** SEO 信息：仅输出到详情页 meta 标签供搜索引擎抓取，前台页面不展示 */
  seo_title?: string | null
  seo_description?: string | null
  seo_keywords?: string | null
  is_published?: boolean
  netdisk_links?: NetdiskLink[]
}

/** 知识节点 */
export interface Node {
  id: number
  name: string
  category: string
  description: string
  content: string
  /** 封面图 URL：前台卡片左侧 1/3 的首图；null = 未上传（回退分类图标底色） */
  cover_image?: string | null
  /** 发布作者：前台卡片与详情页展示；null = 未填写 */
  author_name?: string | null
  /** 作者头像 URL：null/空 = 前台用作者名首字兜底 */
  author_avatar_url?: string | null
  /** 预览量（前台卡片展示；后台列表指标列） */
  view_count?: number
  /** 最近更新时间（后台列表展示；前台不用） */
  updated_at?: string | null
  /** SEO：仅写入详情页 meta 标签，前台不展示 */
  seo_title?: string | null
  seo_description?: string | null
  seo_keywords?: string | null
  is_published?: boolean
}

/** 工具 AI 配置 */
interface ToolAiConfig {
  model?: string
  prompt_template?: string
}

/** 工具表单字段定义 */
export interface ToolFormField {
  name: string
  label: string
  type: string
  placeholder?: string
}

/** 工具表单 schema */
interface ToolFormSchema {
  fields: ToolFormField[]
}

/** 在线工具 */
export interface Tool {
  id: number
  name: string
  description: string
  category: string
  tool_type: 'ai' | 'frontend'
  ai_config?: ToolAiConfig | null
  form_schema?: ToolFormSchema | null
  usage_count: number
  is_published: boolean
  /** 使用权限：free=免费 / auth=需认证 / points=需积分（仅对 AI 工具生效） */
  access_mode?: 'free' | 'auth' | 'points'
  /** 积分模式下单次运行消耗的积分 */
  points_cost?: number
}

/** 扩展 */
export interface Plugin {
  id: number
  name: string
  category: string
  view_count: number
  /** 发布作者（2026-10-12 新增）：前台扩展卡片与详情页展示；null = 未填写 */
  author_name?: string | null
  /** 作者头像 URL：null/空 = 前台用作者名首字兜底 */
  author_avatar_url?: string | null
  description: string
  content: string
  sources?: PluginSource[]
  /** SEO：仅写入详情页 meta 标签，前台不展示 */
  seo_title?: string | null
  seo_description?: string | null
  seo_keywords?: string | null
  is_published?: boolean
  /** 最近更新时间（后台列表展示；前台不用） */
  updated_at?: string | null
}

/** 看板 7 日趋势点（下载量 / 新增注册共用同一结构） */
export interface DashboardTrendPoint {
  /** YYYY-MM-DD（UTC 口径，与后端统计一致） */
  date: string
  count: number
}

/** 看板内容资产计数 */
export interface DashboardContentOverview {
  /** 已发布教程数（新增业务模块时在这里补字段） */
  tutorials: number
}

/** 看板待办计数 */
export interface DashboardPending {
  auth_requests: number
  feedbacks: number
}

/** 看板资源下载榜条目 */

// AI 用量与成本护栏

/** 某一天的 AI 用量明细 */
export interface AiUsageDayPoint {
  date: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  /** 单价未配置时为 null（不显示金额，而不是显示假的 ¥0） */
  estimated_cost: number | null
}

/** 单个模型的用量与费用（按模型分别计价） */
export interface AiUsageModelRow {
  model: string
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  estimated_cost?: number | null
}

/** 「未归因」用量：总量里没有对应模型分桶的部分（本次改动之前写入的历史数据） */
type AiUsageUnattributed = AiUsageModelRow

/** AI 用量汇总（GET /admin/ai-usage 返回） */
export interface AiUsageData {
  today?: Partial<AiUsageDayPoint> & {
    blocked?: number
    by_model?: AiUsageModelRow[]
    unattributed?: AiUsageUnattributed
    /** 成功出图的张数 */
    images?: number
    /** 单价未配置时为 null（不能伪造成 0 元） */
    image_cost?: number | null
    images_by_model?: Array<{ model: string; images: number }>
  }
  month?: Partial<AiUsageDayPoint> & {
    by_model?: AiUsageModelRow[]
    unattributed?: AiUsageUnattributed
  }
  limits?: {
    /** 0 = 未启用该维度上限 */
    daily?: number
    monthly?: number
    /** null = 未设上限 */
    remaining_today?: number | null
    remaining_month?: number | null
    /** 0 = 不限插图张数 */
    image_daily?: number
    /** null = 未设插图上限 */
    remaining_images?: number | null
  }
  pricing?: {
    input_per_mtok?: number
    output_per_mtok?: number
    configured?: boolean
    /** 已单列单价的模型（未列出的按 input/output_per_mtok 兜底价计价） */
    per_model?: Record<string, { input?: number; output?: number }>
  }
  trend?: AiUsageDayPoint[]
}

/** 归一化后的 AI 用量：所有字段必定存在 */
export interface NormalizedAiUsage {
  today: AiUsageDayPoint & {
    blocked: number
    by_model: AiUsageModelRow[]
    /** null = 没有未归因用量（升级后新产生的数据都会归因到具体模型） */
    unattributed: AiUsageUnattributed | null
    images: number
    /** null = 未配置单价（展示为「未配置单价」，不是 ¥0） */
    image_cost: number | null
    images_by_model: Array<{ model: string; images: number }>
  }
  month: AiUsageDayPoint & {
    by_model: AiUsageModelRow[]
    unattributed: AiUsageUnattributed | null
  }
  limits: {
    daily: number
    monthly: number
    remaining_today: number | null
    remaining_month: number | null
    image_daily: number
    remaining_images: number | null
  }
  pricing: {
    input_per_mtok: number
    output_per_mtok: number
    configured: boolean
    per_model: Record<string, { input: number; output: number }>
  }
  trend: AiUsageDayPoint[]
}

/**
 * Dashboard 数据（接口原始返回形态）：仅 5 个 KPI 与下载趋势为一直存在的必填字段，
 * 其余增强字段为可选（可能因旧缓存或旧版本后端缺失），页面统一经 normalizeDashboard() 兜底。
 */
export interface DashboardData {
  today_registrations: number
  today_downloads: number
  conversion_rate: number
  approved_count: number
  download_trend: DashboardTrendPoint[]
  // —— 后端早已返回、此前未在类型中声明（补齐以消除隐式 any） ——
  total_users?: number
  total_tutorials?: number
  registration_source?: { phone: number; email: number }
  // —— 看板增强字段 ——
  registration_trend?: DashboardTrendPoint[]
  active_users_7d?: number
  today_tool_usage?: number
  content_overview?: Partial<DashboardContentOverview>
  pending?: Partial<DashboardPending>
}

/** 归一化后的看板数据：增强字段必定存在，组件内无需判空 */
export interface NormalizedDashboard extends DashboardData {
  total_users: number
  total_tutorials: number
  registration_source: { phone: number; email: number }
  registration_trend: DashboardTrendPoint[]
  active_users_7d: number
  today_tool_usage: number
  content_overview: DashboardContentOverview
  pending: DashboardPending
}

// 系统状态（GET /admin/system-status）
//
// 全字段可选是刻意的：后端是「逐项降级」的聚合接口，某一项读不到时字段缺失或为 null，
// 前端必须能在缺字段时照常渲染（否则一个 Redis 抖动就让整页白屏）。
// 组件只消费 NormalizedSystemStatus（见 dashboardUtils.normalizeSystemStatus）。

/** 单个依赖探针结果：ok=null 表示「未知」（不是失败，不能画成红色） */
export interface SystemStatusCheck {
  ok?: boolean | null
  ms?: number
  error?: string | null
}

/** 定时任务心跳：stale=true 表示「超过预期周期没跑」 */
export interface SystemStatusJob {
  id: string
  /** 展示名；缺省时前端回退到 id（旧版本后端可能没有这个字段） */
  label?: string
  /** 是否参与调度（停用的任务不算「未按时执行」） */
  enabled?: boolean
  /** 上次执行时间（epoch 秒）；null = 从未执行 */
  last_run_at?: number | null
  age_seconds?: number | null
  ok?: boolean | null
  detail?: string
  stale?: boolean
  stale_reason?: string
}

/**
 * 需要人处理的一条提示（level=error 为故障，warn 为「不配也不报错」的漏配）。
 *
 * `category` 由后端下发（dependency / config / job / data / backup）：
 * 以前 10 条提示长得一模一样、只是文字不同，看的人完全不知道该先动哪一条；
 * 分类后前端才能做「故障 / 配置 / 任务 …」筛选。刻意**沿用后端的分类口径**，
 * 不在前端按关键词二次判断（两处判断必然漂移）。
 */
export interface SystemStatusWarning {
  level?: 'error' | 'warn' | string
  category?: string
  category_label?: string
  text?: string
}

export interface SystemStatusData {
  ts?: number
  checks?: {
    mysql?: SystemStatusCheck
    redis?: SystemStatusCheck
    schema?: { code_head?: string | null; db_revision?: string | null; in_sync?: boolean | null }
    logs?: { dir?: string; visible?: boolean; writable?: boolean; last_write_age_minutes?: number | null }
    backup?: {
      dir?: string
      visible?: boolean
      latest?: string | null
      age_hours?: number | null
      stale?: boolean | null
    }
    alerts?: {
      sentry?: boolean
      watchdog_webhook?: boolean
      baidu_push?: boolean
      indexnow?: boolean
    }
    data_quality?: {
      issue_total?: number
      issues?: { code: string; level?: string; count?: number; text?: string }[]
    }
  }
  jobs?: SystemStatusJob[]
  env?: { app_env?: string; release?: string | null; monitoring_enabled?: boolean }
  warnings?: SystemStatusWarning[]
}

// ===== 版本与升级（GET /admin/version） =====

export interface VersionMigrationInfo {
  current?: string | null
  head?: string | null
  in_sync?: boolean | null
  pending?: number | null
  pending_ids?: string[]
}

export interface LatestReleaseInfo {
  configured?: boolean
  checked?: boolean
  tag?: string | null
  name?: string | null
  url?: string | null
  published_at?: string | null
  checked_at?: number | null
  /** 外部检查失败时的可读原因；未配置更新源时为 null */
  error?: string | null
}

export interface VersionData {
  version?: string
  app_env?: string
  app_release?: string | null
  migration?: VersionMigrationInfo
  latest_release?: LatestReleaseInfo
  update_available?: boolean | null
  backup_hint?: string
}

// ===== 备份管理（GET /admin/backups） =====

export interface BackupItem {
  name: string
  size: number
  size_human: string
  /** naive UTC ISO（与其他后台时间字段同一口径） */
  modified_at: string | null
  age_hours: number
  kind: 'auto' | 'pre_restore' | 'other' | string
  has_checksum: boolean
  restore_command: string
}

export interface BackupCommands {
  backup: string
  install_cron: string
  verify: string
  restore_template: string
}

export interface BackupsData {
  dir: string
  visible: boolean
  latest: string | null
  stale: boolean
  stale_hours: number
  count: number
  total_size: number
  items: BackupItem[]
  commands: BackupCommands
  /** 当前管理员是否有下载权限（setting:update） */
  can_download: boolean
}

/** 归一化后的系统状态：字段必定存在，组件内无需判空 */
export interface NormalizedSystemStatus {
  ts: number
  mysql: Required<Pick<SystemStatusCheck, 'ok' | 'ms'>> & { error: string }
  redis: Required<Pick<SystemStatusCheck, 'ok' | 'ms'>> & { error: string }
  schema: { code_head: string; db_revision: string; in_sync: boolean | null }
  logs: { dir: string; visible: boolean; writable: boolean; last_write_age_minutes: number | null }
  backup: {
    dir: string
    visible: boolean
    latest: string
    age_hours: number | null
    stale: boolean | null
  }
  alerts: { sentry: boolean; watchdog_webhook: boolean; baidu_push: boolean; indexnow: boolean }
  dataQuality: {
    /** 巡检结果是否可用：false = 还没跑过 / Redis 里已过期（此时不能显示「无异常」） */
    available: boolean
    issue_total: number
    issues: { code: string; level: string; count: number; text: string }[]
  }
  jobs: {
    id: string
    label: string
    enabled: boolean
    last_run_at: number | null
    ok: boolean | null
    detail: string
    stale: boolean
    stale_reason: string
  }[]
  warnings: { level: string; category: string; category_label: string; text: string }[]
  env: { app_env: string; release: string; monitoring_enabled: boolean }
}

// 定时任务（GET /admin/scheduled-jobs）
//
// 与「系统状态」卡的区别：那边只回答「有没有事」，这里回答「每个任务什么时候跑、
// 上次结果是什么、下次什么时候」。字段全部可选的理由同上 —— 聚合接口逐项降级。

/** 任务依赖的配置项（未配置时任务内部会静默跳过，必须显式提示） */
export interface ScheduledJobRequire {
  key?: string
  configured?: boolean
  /** 归一化时由 key 映射出的中文名 */
  label: string
}

/** 任务状态：ok 正常 / failed 上次失败 / stale 未按时执行 / never 从未执行 / disabled 已停用 */
export type ScheduledJobStatus = 'ok' | 'failed' | 'stale' | 'never' | 'disabled'

/** 可配置部分（编辑弹窗的表单初始值） */
export interface ScheduledJobConfig {
  enabled?: boolean
  schedule_kind?: string
  hour?: number | null
  minute?: number | null
  interval_minutes?: number | null
  schedule?: string
}

export interface ScheduledJob {
  id?: string
  label?: string
  category?: string
  desc?: string
  /** 人类可读周期，如「每日 02:00」 */
  schedule?: string
  schedule_kind?: string
  /** 与代码默认值不同 = 被人改过 */
  customized?: boolean
  default_schedule?: string
  enabled?: boolean
  config?: ScheduledJobConfig
  /** 多久没跑算超期（秒），用于抽屉里的口径说明 */
  stale_seconds?: number
  note?: string
  /** 上次执行（epoch 秒）；null = 从无心跳记录 */
  last_run_at?: number | null
  last_age_seconds?: number | null
  ok?: boolean | null
  /** 上次执行的产出摘要，如「降级 2 个用户」 */
  detail?: string
  status?: string
  status_text?: string
  stale_reason?: string
  /** 下次预计执行（epoch 秒）；停用时为 null */
  next_run_at?: number | null
  requires?: { key?: string; configured?: boolean }[]
}

export interface ScheduledJobGroup {
  key?: string
  label?: string
  jobs?: ScheduledJob[]
}

export interface ScheduledJobsData {
  ts?: number
  timezone?: string
  heartbeat_ttl_hours?: number
  summary?: {
    total?: number
    ok?: number
    failed?: number
    stale?: number
    never?: number
    disabled?: number
    attention?: number
  }
  /** 全部任务都没有心跳 —— 一条独立结论（调度器容器没起 / Redis 地址错），不是 8 条告警 */
  all_silent?: boolean
  /**
   * 配置生效状态（调度器写回的 ops:jobs:applied）
   * pending = 已存库但调度器还没对齐；alive = 调度器最近 60 秒有心跳
   */
  apply?: { pending?: boolean; applied_at?: number | null; alive?: boolean; hash?: string }
  /** 配置表是否可用（false = 迁移未执行，页面按代码默认值展示） */
  configs_available?: boolean
  run_cooldown_seconds?: number
  groups?: ScheduledJobGroup[]
}

export interface NormalizedScheduledJobGroup {
  key: string
  label: string
  jobs: NormalizedScheduledJob[]
}

export interface NormalizedScheduledJob {
  id: string
  label: string
  desc: string
  schedule: string
  scheduleKind: string
  customized: boolean
  defaultSchedule: string
  enabled: boolean
  config: { scheduleKind: string; hour: number; minute: number; intervalMinutes: number }
  staleSeconds: number
  note: string
  lastRunAt: number | null
  lastAgeSeconds: number | null
  ok: boolean | null
  detail: string
  status: ScheduledJobStatus
  statusText: string
  staleReason: string
  nextRunAt: number | null
  requires: ScheduledJobRequire[]
}

export interface NormalizedScheduledJobs {
  ts: number
  timezone: string
  heartbeatTtlHours: number
  summary: {
    total: number
    ok: number
    failed: number
    stale: number
    never: number
    disabled: number
    attention: number
  }
  allSilent: boolean
  apply: { pending: boolean; appliedAt: number | null; alive: boolean }
  configsAvailable: boolean
  runCooldownSeconds: number
  groups: NormalizedScheduledJobGroup[]
}

// 转化漏斗与留存（GET /admin/funnel）
//
// 阶段口径是**嵌套**的：后一阶段只数走完了前面所有阶段的人，所以 count 单调递减。
// count 为 null = 该阶段聚合失败（未知），**不是 0** —— 组件必须区分显示。

export interface FunnelStage {
  key?: string
  label?: string
  count?: number | null
  /** 相对队列起点（注册）的累计转化率 0~1 */
  rate_from_start?: number | null
  /** 相对上一阶段的阶段间转化率 0~1（定位流失点的关键） */
  rate_from_prev?: number | null
}

export interface FunnelData {
  days?: number
  since?: string
  stages?: FunnelStage[]
  approval_rate?: number | null
  paid_rate?: number | null
}

export interface RetentionRow {
  date?: string
  cohort?: number
  d1_active?: number | null
  d7_active?: number | null
  d1_rate?: number | null
  d7_rate?: number | null
}

export interface FunnelOverviewData {
  funnel?: FunnelData
  retention?: { days?: number; rows?: RetentionRow[] }
}

/** 归一化后的漏斗：字段必定存在，组件内无需判空 */
export interface NormalizedFunnel {
  days: number
  since: string
  stages: {
    key: string
    label: string
    count: number | null
    rateFromStart: number | null
    rateFromPrev: number | null
  }[]
  approvalRate: number | null
  paidRate: number | null
  retentionDays: number
  retention: {
    date: string
    cohort: number
    d1Active: number | null
    d7Active: number | null
    d1Rate: number | null
    d7Rate: number | null
  }[]
}

// 站内搜索词报告（GET /admin/search-keywords）

export interface SearchKeywordRow {
  keyword?: string
  count?: number
  zero_count?: number
  avg_results?: number | null
}

export interface SearchZeroRow {
  keyword?: string
  count?: number
  last_at?: string | null
}

export interface SearchKeywordReport {
  days?: number
  /** false = 还没采集到数据（或迁移未执行），页面据此显示「尚未采集」而不是空榜 */
  available?: boolean
  total?: number
  unique_keywords?: number
  zero_result_total?: number
  zero_result_rate?: number | null
  top?: SearchKeywordRow[]
  zero_result?: SearchZeroRow[]
  modules?: { module?: string; count?: number }[]
}

// 内容归因（GET /admin/content-attribution）
//
// 口径：看过该内容的登录用户中，有多少人在 ATTRIBUTION_WINDOW_DAYS（默认 7 天）内提交了认证申请。

export interface AttributionItem {
  target_type?: string
  target_id?: number
  title?: string
  viewers?: number
  converted?: number
  /** 转化率 0~1；viewers 为 0 时为 null（不是 0） */
  rate?: number | null
}

export interface ContentAttributionData {
  days?: number
  /** false = 还没采集到数据（或迁移未执行） */
  available?: boolean
  attribution_window_days?: number
  total_views?: number
  total_viewers?: number
  items?: AttributionItem[]
}

/** 归一化后的归因数据：字段必定存在，组件内无需判空 */
export interface NormalizedAttribution {
  days: number
  available: boolean
  windowDays: number
  totalViews: number
  totalViewers: number
  items: { type: string; id: number; title: string; viewers: number; converted: number; rate: number | null }[]
}

// RBAC 权限管理

/** RBAC 角色（管理后台权限聚合体） */
export interface RbacRole {
  id: number
  code: string
  name: string
  description: string | null
  is_system: boolean
  /** 角色已绑定的权限码（列表接口返回） */
  permissions?: string[]
  /** 使用该角色的用户数（列表接口返回） */
  user_count?: number
  /** 数据范围：all=全部 / dept=本部门 / self=仅本人 */
  data_scope?: 'all' | 'dept' | 'self'
}

/** 权限点 */
export interface RbacPermission {
  code: string
  name: string
}

/** 权限目录树（按模块分组，角色权限分配界面用） */
export interface RbacPermissionGroup {
  module: string
  name: string
  menu: RbacPermission | null
  buttons: RbacPermission[]
}

/** 部门 */
export interface Department {
  id: number
  name: string
  parent_id: number | null
  sort_order: number
  user_count?: number
  created_at: string | null
}

/** 当前管理员 RBAC 档案（GET /admin/rbac/profile） */
export interface RbacProfile {
  user: { id: number; account: string; nickname: string | null; avatar_url: string | null }
  roles: { id: number; code: string; name: string }[]
  permissions: string[]
  is_super: boolean
  department: { id: number; name: string } | null
}

/**
 * 侧边菜单叶子项（GET /admin/menus，配置源 `backend/app/services/admin_menu.py`）。
 * - `key`：SPA 路由路径；外链为任意唯一 key
 * - `perm` / `perms`：RBAC 权限码（单一 / 任一命中），都不带 = 人人可见
 * - `icon`：图标名，由 `constants/menuIcons` 映射成组件
 * - `url`：非空 = 外链，新窗口打开
 */
export interface MenuLeaf {
  key: string
  label: string
  icon: string
  perm?: string
  perms?: string[]
  url?: string
}

/** 侧边菜单分组（父级，渲染为可折叠子菜单） */
export interface MenuGroup {
  key: string
  label: string
  icon: string
  items: MenuLeaf[]
}

/** 侧边菜单树：分组 + 分组外的独立项 */
export interface MenuTree {
  groups: MenuGroup[]
  footer: MenuLeaf[]
}

// ===== 顶栏待办（GET /admin/todos） =====
//
// 只含「当前管理员有权限处理且计数 > 0」的类别；无权限的类别由后端直接裁掉。
// 前端不做二次权限判断，也不复制各业务域的计数口径。
export type TodoSeverity = 'warning' | 'error'

export interface TodoGroup {
  key: string
  label: string
  count: number
  /** 站内相对路径，点击后跳到原业务页面处理 */
  path: string
  severity: TodoSeverity
  /** 补充说明（失败任务名、额度用量等），可能为空串 */
  detail: string
}

export interface TodoSummary {
  total: number
  groups: TodoGroup[]
}

// ===== 统一回收站 =====
//
// 教程 / 评论 / 用户三类软删除记录共用同一形状；`retention_days = null`
// 表示不自动清理（用户账号、有购买记录的教程）。
export interface RecycleBinTypeSummary {
  type: 'tutorial' | 'comment' | 'user'
  type_label: string
  count: number
  retention_days: number | null
  can_restore: boolean
}

export interface RecycleBinSummary {
  total: number
  items: RecycleBinTypeSummary[]
}

export interface RecycleBinItem {
  type: 'tutorial' | 'comment' | 'user'
  type_label: string
  id: number
  title: string
  subtitle: string
  deleted_at: string | null
  retention_days: number | null
  expires_at: string | null
  remaining_days: number | null
  retention_note: string
  can_restore: boolean
}

// ===== 部署体检（GET /admin/readiness） =====
//
// 状态语义由后端定义，前端只做呈现：
//   ready        配好了，能用
//   unconfigured 没配（required=true 时算「阻塞首发」）
//   error        配了但不完整/不可用 —— 比没配更危险，用户以为自己配好了
//   off          功能明确关闭（不是问题）
export type ReadinessStatus = 'ready' | 'unconfigured' | 'error' | 'off'

export interface ReadinessItem {
  key: string
  label: string
  status: ReadinessStatus
  /** 必填项未就绪 → 阻塞上线；可选项没配只影响对应功能 */
  required: boolean
  /** 一句话结论：此刻是什么状态 */
  detail: string
  /** 下一步做什么（可空） */
  action: string
  /** 前端跳转路径（可空，例如 /mail-settings） */
  link: string
}

export interface ReadinessSummary {
  total: number
  ready: number
  off: number
  error: number
  /** 只统计「必填项未就绪」，可选项不算 */
  blocking: number
  blocking_keys: string[]
  deployable: boolean
}

export interface ReadinessData {
  ts: number
  items: ReadinessItem[]
  summary: ReadinessSummary
  storage_driver: string
  app_env: string
}
