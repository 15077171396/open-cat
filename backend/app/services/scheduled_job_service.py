"""定时任务目录 + 状态计算（后台「定时任务」页与「系统状态」卡共用）。

为什么要有这份目录，而不是直接问调度器「你有哪些任务」：
调度器跑在**独立容器**里（`python -m scheduler.tasks`），API 进程内没有 Scheduler 实例，
读不到它的 jobstore。两侧唯一的连接点是 Redis 心跳 —— `scheduler/tasks.py::_with_heartbeat`
在任务结束后调 `system_status_service.record_job_run()` 写一条 `ops:job:<id>`。

所以本模块做三件事：
1. **静态声明**「应该有哪些任务、什么周期、多久不跑算超期、依赖哪些配置」（`JOB_SPECS`）；
2. **纯函数**算「下次执行时间」（`next_run_at`，`now` 由调用方传入，便于离线单测）；
3. 把心跳 + 目录**合并**成页面需要的四态（`build_overview`）。

⚠️ 四条硬约束（改之前先读）：
- `JOB_SPECS` 的 id 必须与 `scheduler/tasks.py` 的 `JOB_FUNCS` **一一对应**。
  对不上的后果极其隐蔽：后台永远显示「从未执行」，看起来像任务没跑，实际只是名字写错了。
  `tests/test_scheduled_jobs.py` 读源码双向比对，这里写错会直接变红。
- **未知 ≠ 失败**：心跳读不到（Redis 挂了 / TTL 过期 / 容器没起）是「没有记录」，
  与「执行失败」分开成两种状态，否则调度器一次重启就会刷出一片假故障。
- **任务体永远在代码里**：后台能改的只有「跑不跑 / 什么周期」，任务体（`JOB_FUNCS` 里的 handler）
  仍必须写代码并过测试。UI 上的「新增任务」= 从注册表里挑一个，不是凭空造逻辑 ——
  真正需要新逻辑时仍然是一次正常的代码变更，这才是可审查、可回滚的方式。
- **跨进程下发只有两条通道**（都在这份文件里定义键名）：
  1. 配置表 `scheduled_job_configs` → 调度器每 5 秒对齐一次（见 `scheduler/tasks.py`）；
  2. Redis 命令队列 `ops:jobs:commands` → 调度器每秒取一条执行（「立即执行一次」）。
  调度器把「我按哪份配置在跑」写回 `ops:jobs:applied`（TTL 60s），
  既是**配置生效状态**也是**调度器存活信号** —— 页面据此提示「待生效 / 调度器未运行」。
  不要在 API 进程里直接调 handler：那会绕过 max_instances、心跳与容器隔离。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import NamedTuple
from datetime import datetime, timedelta, timezone

# 定时任务固定跑在**北京时间**（`scheduler/tasks.py` 的 CronTrigger 显式指定 Asia/Shanghai）。
#
# 这里用固定 +08:00 而不是 ZoneInfo("Asia/Shanghai")：中国 1991 年起不再实行夏令时，
# 固定偏移与 IANA 库在任意现代日期上结果一致；而 Windows 本地开发环境常常没有 IANA
# 时区库（`tzdata` 未安装），用 ZoneInfo 会让「后台页面打不开」发生在这类运维页上 ——
# 恰恰是最不该出问题的地方。
CST = timezone(timedelta(hours=8))

# 心跳保留 3 天：只用于判断「最近有没有跑」，不是审计留痕
# （真正的执行审计在应用日志与 admin_audit_logs，不要指望这里）
HEARTBEAT_TTL_HOURS = 72

# 调度器「当前按哪份配置在跑」的写回键（每 5 秒重写，TTL 120 秒 —— 比判定阈值宽一倍，
# 免得刚好卡在边界上抖动）。它同时是**调度器存活信号**。
APPLIED_KEY = "ops:jobs:applied"
APPLIED_TTL_SECONDS = 120

# 「立即执行一次」的命令队列：API 侧 LPUSH，调度器侧每秒 LPOP 消费。
# 用队列而不是让 API 直接调 handler：执行必须在调度器容器里发生（见模块文档第 4 条）。
COMMAND_QUEUE_KEY = "ops:jobs:commands"
# 队列长度上限：防止有人刷接口把命令堆起来（正常一次点击就一条）
COMMAND_QUEUE_MAX = 50

# 任务分组顺序 = 页面展示顺序。按「出问题时先看哪一类」排：
# 权益 → 分发 → 存储 → 巡检
CATEGORY_USER = "user"
CATEGORY_DISTRIBUTION = "distribution"
CATEGORY_STORAGE = "storage"
CATEGORY_QUALITY = "quality"

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_USER: "用户与权益",
    CATEGORY_DISTRIBUTION: "内容分发",
    CATEGORY_STORAGE: "数据与存储",
    CATEGORY_QUALITY: "数据巡检",
}

# 超期阈值：比调度周期留足余量。
# 日报任务 26h（容忍一次 misfire 补跑 + 时区误差 + 执行耗时）；每分钟任务 30min（容忍容器重启）。
_DAILY_STALE_SECONDS = 26 * 3600
_INTERVAL_STALE_SECONDS = 30 * 60


class JobSpec(NamedTuple):
    """一个定时任务的静态说明（不含任何运行状态）"""

    id: str
    """与 `scheduler/tasks.py::add_job(id=...)` 完全一致的注册名"""

    label: str
    """页面上的任务名（同时也是「系统状态」卡里 chip 的文案）"""

    category: str
    """所属分组（见 CATEGORY_*）"""

    desc: str
    """做什么、为什么要做 —— 页面上给不熟悉这套任务的人看"""

    kind: str
    """调度类型：`daily`（每天固定时刻）或 `interval`（固定间隔）"""

    hour: int = 0
    """daily：执行小时（北京时间）"""

    minute: int = 0
    """daily：执行分钟（北京时间）"""

    interval_minutes: int = 0
    """interval：间隔分钟数"""

    stale_seconds: int = _DAILY_STALE_SECONDS
    """多久没跑算「未按时执行」"""

    requires: tuple[str, ...] = ()
    """依赖的配置项（settings 属性名）。未配置时任务内部会**静默跳过**，必须显式提示"""

    note: str = ""
    """补充说明（保留期 / 上限等业务口径），没有就留空"""

    @property
    def schedule_text(self) -> str:
        """人类可读的调度周期：每日 02:00 / 每 1 分钟"""
        if self.kind == "interval":
            return f"每 {self.interval_minutes} 分钟"
        return f"每日 {self.hour:02d}:{self.minute:02d}"


JOB_SPECS: tuple[JobSpec, ...] = (
    JobSpec(
        id="check_vip_expiry",
        label="VIP 过期降级",
        category=CATEGORY_USER,
        desc="把到期未降级的 VIP 账号改回普通用户，避免过期后仍享付费权益",
        kind="daily",
        hour=2,
        minute=0,
    ),
    JobSpec(
        id="push_new_urls_to_baidu",
        label="百度收录推送",
        category=CATEGORY_DISTRIBUTION,
        desc="把近 26 小时新增/更新的内容页推给百度，加速被收录（存量页靠 sitemap 兜底）",
        kind="daily",
        hour=3,
        minute=0,
        requires=("BAIDU_PUSH_TOKEN",),
        note="未配置 token 时任务会直接跳过并返回「已跳过」，不算失败",
    ),
    JobSpec(
        id="push_new_urls_to_indexnow",
        label="IndexNow 收录推送",
        category=CATEGORY_DISTRIBUTION,
        desc="同上，推送给 Bing 等支持 IndexNow 的搜索引擎",
        kind="daily",
        hour=3,
        minute=10,
        requires=("INDEXNOW_KEY",),
        note="未配置密钥时任务会直接跳过并返回「已跳过」，不算失败",
    ),
    JobSpec(
        id="cleanup_audit_logs",
        label="审计日志清理",
        category=CATEGORY_STORAGE,
        desc="按保留期删除过期的管理操作审计记录（审计表只增不减，必须定期清理）",
        kind="daily",
        hour=4,
        minute=0,
        note="保留 365 天",
    ),
    JobSpec(
        id="cleanup_search_logs",
        label="搜索日志清理",
        category=CATEGORY_STORAGE,
        desc="清理过期的站内搜索词记录。搜索日志是选题素材，留太久只白占磁盘",
        kind="daily",
        hour=4,
        minute=30,
        note="保留 90 天",
    ),
    JobSpec(
        id="cleanup_content_views",
        label="浏览归因清理",
        category=CATEGORY_STORAGE,
        desc="清理过期的内容浏览归因记录（「看过什么 → 7 天内申请认证」的数据源）",
        kind="daily",
        hour=4,
        minute=40,
        note="保留 180 天：看内容到决定付费的周期通常以周计，太短会删掉有效归因",
    ),
    JobSpec(
        id="flush_view_counts",
        label="浏览量落库",
        category=CATEGORY_STORAGE,
        desc="详情页浏览量在 Redis 累加，由本任务合并成一条 UPDATE 写回，把写放大降到 1/60",
        kind="interval",
        interval_minutes=1,
        stale_seconds=_INTERVAL_STALE_SECONDS,
        note="间隔超过 2 分钟没有写入，说明调度器或 Redis 有问题（数据库里的浏览量会停更）",
    ),
    JobSpec(
        id="data_quality_check",
        label="数据质量巡检",
        category=CATEGORY_QUALITY,
        desc="只查不删：负积分、过期未降级的 VIP、指向已删内容的孤儿收藏/下载/学习记录",
        kind="daily",
        hour=5,
        minute=0,
        note="结果写入 Redis，由「系统状态」卡展示明细",
    ),
)

# 兼容旧调用点（`system_status_service` / `tests/test_system_status.py` 用的是 (id, label) 二元组）
JOBS: tuple[tuple[str, str], ...] = tuple((spec.id, spec.label) for spec in JOB_SPECS)

# 兼容旧调用点：id → 超期阈值（秒）
STALE_SECONDS: dict[str, int] = {spec.id: spec.stale_seconds for spec in JOB_SPECS}

# 状态文案。`never` 与 `stale` 分开：前者是「这个任务从来没跑过」（多半是调度器没起或名字写错），
# 后者是「跑过但超期了」（多半是任务卡住或调度器中途重启）；`disabled` 是**人主动关的**。
STATUS_TEXT: dict[str, str] = {
    "ok": "正常",
    "failed": "上次失败",
    "stale": "未按时执行",
    "never": "从未执行",
    "disabled": "已停用",
}

# 排序权重：异常在前（页面默认只看异常时顺序也有意义）；已停用排最后 —— 它是人为决定，不是问题
_STATUS_ORDER = {"failed": 0, "stale": 1, "never": 2, "ok": 3, "disabled": 4}

# 「调度器最近一次对齐配置」的距离：超过这个秒数就认为调度器没在跑
# （对齐循环每 5 秒一次，60 秒足够容忍一次 GC/慢查询卡顿）
APPLIED_STALE_SECONDS = 60


# ---------------------------------------------------------------------------
# 一、可配置项（enabled / 周期）
# ---------------------------------------------------------------------------
#
# 为什么配置与目录分开：`JobSpec` 是**代码定义**的默认值（写死在仓库里、可被单测断言），
# `JobConfig` 是**库里可改**的覆盖值。两者合并后才是一次真实调度，页面同时要能看到
# 「现在跑什么」和「是不是被改过」。


class JobConfig(NamedTuple):
    """一个任务的可配置部分（默认值来自 `JobSpec`，后台可改）"""

    enabled: bool
    kind: str
    hour: int
    minute: int
    interval_minutes: int

    @property
    def schedule_text(self) -> str:
        """人类可读的周期：每日 02:00 / 每 1 分钟"""
        if self.kind == "interval":
            return f"每 {self.interval_minutes} 分钟"
        return f"每日 {self.hour:02d}:{self.minute:02d}"

    def as_dict(self) -> dict:
        """接口载荷（页面编辑弹窗直接用它填表单）"""
        return {
            "enabled": self.enabled,
            "schedule_kind": self.kind,
            "hour": self.hour,
            "minute": self.minute,
            "interval_minutes": self.interval_minutes,
            "schedule": self.schedule_text,
        }


def default_config(spec: JobSpec) -> JobConfig:
    """代码里的默认配置（表里没这行 / 读库失败时都用它）"""
    return JobConfig(
        enabled=True,
        kind=spec.kind,
        hour=spec.hour,
        minute=spec.minute,
        interval_minutes=spec.interval_minutes,
    )


# 启动即用的默认配置表：调度器读不到库时靠它继续跑（宁可跑默认周期，也不要整片停摆）
DEFAULT_CONFIGS: dict[str, JobConfig] = {spec.id: default_config(spec) for spec in JOB_SPECS}


def config_from_row(spec: JobSpec, row: dict | None) -> JobConfig:
    """库里的行 → 配置；缺行 / 字段脏 / 取值越界一律回退默认值。

    刻意**不做「报错让人来修」**：一条脏配置不该让整个调度器起不来，
    而回退默认的行为在页面上看得见（会显示成默认周期，与「改过的」区分开）。
    """
    base = default_config(spec)
    if not row:
        return base

    enabled = row.get("enabled")
    kind = row.get("schedule_kind")
    if kind not in ("daily", "interval"):
        kind = base.kind

    def _int(value, fallback: int, low: int, high: int) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return fallback
        return n if low <= n <= high else fallback

    if kind == "interval":
        return JobConfig(
            enabled=enabled is not False,
            kind="interval",
            hour=base.hour,
            minute=base.minute,
            interval_minutes=_int(row.get("interval_minutes"), base.interval_minutes or 1, 1, 10080),
        )
    return JobConfig(
        enabled=enabled is not False,
        kind="daily",
        hour=_int(row.get("hour"), base.hour, 0, 23),
        minute=_int(row.get("minute"), base.minute, 0, 59),
        interval_minutes=base.interval_minutes,
    )


def config_view(row) -> dict | None:
    """ORM 行（或已经是字典的）→ `config_from_row` 认的字典。

    ORM 对象没有 `.get`，而 `config_from_row` 只认 dict —— 这层适配让**调度器**与
    **管理接口**共用同一份「行 → 配置」的解析与容错，不用各写一份。
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return {
        "enabled": getattr(row, "enabled", None),
        "schedule_kind": getattr(row, "schedule_kind", None),
        "hour": getattr(row, "hour", None),
        "minute": getattr(row, "minute", None),
        "interval_minutes": getattr(row, "interval_minutes", None),
    }


def config_fingerprint(configs: dict[str, JobConfig]) -> str:
    """配置指纹：调度器与接口两侧用它判断「库里的配置是不是还没生效」。

    取 sha1 前 12 位而不是自增版本号：**手改数据库**也会被发现（版本号只在自己写入时递增，
    运维直接 UPDATE 一行就会漏掉），而指纹是对结果的哈希，来源无关。
    """
    payload = json.dumps(
        {
            job_id: [cfg.enabled, cfg.kind, cfg.hour, cfg.minute, cfg.interval_minutes]
            for job_id, cfg in sorted(configs.items())
        },
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def scheduler_alive(applied: dict | None, now: float) -> bool:
    """调度器是否还活着（`ops:jobs:applied` 是它每 5 秒重写一次的心跳）。

    单独抽出来是因为「下发命令前必须确认有人消费」：队列里的命令不会自己执行，
    调度器没在跑时把命令塞进队列，用户会以为已经手动跑过了 —— 比直接报错糟糕得多。
    """
    if not applied:
        return False
    try:
        at = int(applied.get("at") or 0)
    except (TypeError, ValueError):
        return False
    return bool(at) and (int(now) - at) <= APPLIED_STALE_SECONDS


def apply_state(configs: dict[str, JobConfig], applied: dict | None, now: float) -> dict:
    """「配置是否已生效 + 调度器是否活着」，都由 `ops:jobs:applied` 这一个键回答。

    `applied` 由调度器的对齐循环**每 5 秒重写一次**（TTL 120 秒），所以：
    - `alive=False` → 调度器容器没起 / 挂了（此时「立即执行」也不可能成功）；
    - `pending=True` → 配置已存库但调度器还没轮到（≤5 秒），页面必须提示，否则用户会以为没保存。
    """
    current = config_fingerprint(configs)
    if not applied:
        return {"pending": True, "applied_at": None, "alive": False, "hash": current}

    try:
        at = int(applied.get("at") or 0)
    except (TypeError, ValueError):
        at = 0
    return {
        "pending": applied.get("hash") != current,
        "applied_at": at or None,
        "alive": scheduler_alive(applied, now),
        "hash": current,
    }


def next_run_at(
    spec: JobSpec,
    now: float,
    last_run_at: int | None = None,
    config: JobConfig | None = None,
) -> int | None:
    """下一次（预计）执行时间，epoch 秒；无意义时返回 None。

    **纯函数**：`now` 由调用方传入，不读时钟 —— 否则「明天 02:00」这类断言只能在
    特定时刻跑通，测试会变成随时会红的定时炸弹。

    daily：取北京时间今天/明天的 hour:minute（今天这个点已过就顺延一天）。
    interval：以「上次执行 + 间隔」为准；上次执行太久远（比如停机三天）则按「现在 + 间隔」，
    否则会显示一个早就该发生的过去时间点。

    `config` 传空则用代码默认值 —— 保存过的周期优先，页面显示的必须和实际调度一致。
    """
    cfg = config or default_config(spec)
    if cfg.kind == "interval":
        if cfg.interval_minutes <= 0:
            return None
        step = cfg.interval_minutes * 60
        candidate = (last_run_at or 0) + step
        return int(candidate if candidate > now else now + step)

    local_now = datetime.fromtimestamp(now, CST)
    candidate = local_now.replace(hour=cfg.hour, minute=cfg.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


def build_job(
    spec: JobSpec,
    heartbeat: dict | None,
    now: float,
    configured: dict[str, bool],
    config: JobConfig | None = None,
) -> dict:
    """单条任务载荷：目录 + 配置 + 心跳 + 派生状态。

    `heartbeat` 为 None = 没有心跳记录（Redis 不可用 / TTL 过期 / 从未执行），
    一律归到 `never`，**不当成失败** —— 心跳是观测手段，观测缺失不代表任务挂了。
    """
    cfg = config or default_config(spec)
    at = 0
    ok: bool | None = None
    detail = ""
    if heartbeat:
        try:
            at = int(heartbeat.get("at") or 0)
        except (TypeError, ValueError):
            at = 0
        ok = heartbeat.get("ok")
        detail = str(heartbeat.get("detail") or "")[:200]

    age: int | None = int(now) - at if at else None

    if not cfg.enabled:
        # 停用优先于一切：它不跑是**预期行为**，不能算「未按时执行」去刷告警
        status = "disabled"
        stale_reason = "已停用，不会按周期执行"
    elif not at:
        status = "never"
        stale_reason = "无执行记录（任务可能从未运行，或心跳已过期）"
    elif ok is False:
        status = "failed"
        stale_reason = ""
    elif age is not None and age > spec.stale_seconds:
        status = "stale"
        stale_reason = f"已 {round(age / 3600, 1)} 小时未执行（阈值 {round(spec.stale_seconds / 3600, 1)} 小时）"
    else:
        status = "ok"
        stale_reason = ""

    return {
        "id": spec.id,
        "label": spec.label,
        "category": spec.category,
        "desc": spec.desc,
        "schedule": cfg.schedule_text,
        "schedule_kind": cfg.kind,
        # 与代码默认值不同 = 被人改过，页面标「已调整」（排查时先看这里有没有被改）
        "customized": cfg != default_config(spec),
        "default_schedule": default_config(spec).schedule_text,
        "enabled": cfg.enabled,
        "config": cfg.as_dict(),
        "stale_seconds": spec.stale_seconds,
        "note": spec.note,
        "last_run_at": at or None,
        "last_age_seconds": age,
        "ok": ok,
        "detail": detail,
        "status": status,
        "status_text": STATUS_TEXT[status],
        "stale_reason": stale_reason,
        "next_run_at": next_run_at(spec, now, at or None, cfg) if cfg.enabled else None,
        "requires": [
            {"key": key, "configured": bool(configured.get(key))} for key in spec.requires
        ],
    }


def build_overview(
    heartbeats: dict[str, dict | None],
    now: float,
    configured: dict[str, bool],
    configs: dict[str, JobConfig] | None = None,
    applied: dict | None = None,
) -> dict:
    """页面载荷：summary + 分组列表 + 「全局静默」+ 「配置生效状态」。

    `all_silent`：所有**启用中**的任务都没有心跳。这是**一条独立的诊断结论** ——
    任务集体失联几乎只可能是调度器容器没起 / Redis 地址错了，
    逐个列出 8 条「从未执行」只会让人一头雾水（「系统状态」卡以前就是这个毛病）。

    `configs` 传空即全用代码默认值（库里没数据 / 读库失败时也照常出页面）。
    """
    effective = configs or DEFAULT_CONFIGS
    items = [
        build_job(spec, heartbeats.get(spec.id), now, configured, effective.get(spec.id))
        for spec in JOB_SPECS
    ]

    summary = {
        "total": len(items),
        "ok": sum(1 for item in items if item["status"] == "ok"),
        "failed": sum(1 for item in items if item["status"] == "failed"),
        "stale": sum(1 for item in items if item["status"] == "stale"),
        "never": sum(1 for item in items if item["status"] == "never"),
        "disabled": sum(1 for item in items if item["status"] == "disabled"),
    }
    # 需要人处理的 = 除「正常」与「已停用（人主动关的）」之外的全部
    summary["attention"] = summary["total"] - summary["ok"] - summary["disabled"]

    groups: list[dict] = []
    for key, label in CATEGORY_LABELS.items():
        members = sorted(
            (item for item in items if item["category"] == key),
            key=lambda item: (_STATUS_ORDER.get(item["status"], 9), item["id"]),
        )
        if members:
            groups.append({"key": key, "label": label, "jobs": members})

    enabled_count = summary["total"] - summary["disabled"]
    enabled_never = sum(1 for item in items if item["status"] == "never")

    return {
        "ts": int(now),
        "timezone": "UTC+08:00（北京时间）",
        "heartbeat_ttl_hours": HEARTBEAT_TTL_HOURS,
        "summary": summary,
        # 只有「启用的任务全都无心跳」才算全局静默：只剩一个启用的任务在跑时，全员静默是误报
        "all_silent": enabled_count > 0 and enabled_never == enabled_count,
        "groups": groups,
        "apply": apply_state(effective, applied, now),
    }


def now_epoch() -> float:
    """当前 epoch 秒（单独抽出来，方便测试时替换）"""
    return time.time()
