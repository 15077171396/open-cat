"""运维状态聚合：把「必须 SSH 上去看」的信息搬到后台（「系统状态」卡的数据源）。

设计目标只有一个 —— 让**不在服务器前的人**也能回答两个问题：
「现在有没有事？」「有没有哪一环其实早就悄悄停了？」

三条硬约束（改这个文件前请先读）：

1. **全程只读**：不写任何业务表，最坏结果是少显示一项，绝不改变线上行为。
2. **逐项降级**：任一子项抛异常只影响它自己（记 WARNING 后返回 None），
   绝不让整个接口 500。「运维页打不开」比「少一项指标」严重得多 ——
   前者会在最需要它的时候把人挡在门外。
3. **不新增权限码**：复用 `dashboard:view`。新增权限码必须配套「补种给已有内置角色」
   的迁移，否则已有部署升级后管理员立刻 403（见 `core/rbac.py` 的同款注释）。

数据来源分三类：
- 实时探测：复用 `health_service.get_deep_health()`（MySQL / Redis / 库版本，自带 5s 缓存）；
- 文件系统：日志目录、备份目录（容器内为只读挂载）；
- Redis 心跳：定时任务每次执行后由 `record_job_run()` 写入（见 `scheduler/tasks.py`）。

⚠️ 心跳的 TTL 是 3 天：它只用于「最近有没有跑」，不是审计留痕。
真正的执行审计在应用日志与 `admin_audit_logs`，不要指望这里。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from loguru import logger

from app.config import settings
from app.core.degrade import degrade_guard
from app.redis import log_redis_degraded, redis_client
from app.services import health_service, scheduled_job_service

# 结果缓存键：与 health.deep 分开，避免互相覆盖（两者缓存时长不同）
_CACHE_KEY = "ops:system_status"

# 心跳键前缀：`ops:job:<job_id>` → JSON {"at": epoch, "ok": bool, "detail": str}
_JOB_KEY_PREFIX = "ops:job:"
# 公开别名：其他运维服务（如 scheduled_job_admin_service）读心跳时用它，避免各自拼字符串
HEARTBEAT_KEY_PREFIX = _JOB_KEY_PREFIX
_JOB_TTL_SECONDS = 3 * 24 * 3600

# 数据巡检结果键（由 scheduler 的 data_quality_check 写入）
_QUALITY_KEY = "ops:data_quality"

# 定时任务清单：**唯一事实来源是 `services/scheduled_job_service.py`**
# （那里同时还带着调度周期、超期阈值、依赖配置、说明，供「定时任务」页使用）。
# 本模块只做兼容导出：既有调用点（心跳写入、单测）用的仍是 (id, label) 二元组。
JOBS: tuple[tuple[str, str], ...] = scheduled_job_service.JOBS

# 每个任务的「多久没跑就算异常」（秒），同样来自目录
_JOB_STALE_SECONDS: dict[str, int] = scheduled_job_service.STALE_SECONDS


async def record_job_run(job_id: str, ok: bool, detail: str = "") -> None:
    """记录一次任务执行（供后台「系统状态」展示）。

    **绝不抛异常**：心跳是观测手段，观测失败不能反过来让被观测的任务失败。
    Redis 不可用时只记降级日志，任务照常结束。
    """
    payload = json.dumps({"at": int(time.time()), "ok": bool(ok), "detail": detail[:200]})
    try:
        await redis_client.set(f"{_JOB_KEY_PREFIX}{job_id}", payload, ex=_JOB_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 观测失败不得影响任务本身
        log_redis_degraded("system_status.record_job_run", f"任务心跳写入失败: {job_id} ({exc})")


async def record_data_quality(result: dict) -> None:
    """缓存最近一次数据巡检结果（同样绝不抛异常）"""
    try:
        await redis_client.set(_QUALITY_KEY, json.dumps(result), ex=_JOB_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        log_redis_degraded("system_status.record_data_quality", f"巡检结果写入失败: {exc}")


def _log_dir_status() -> dict:
    """日志目录：是否可写 + 最后一个文件多久之前写过。

    「最后写入时间」比「目录存在」有用得多：容器还在跑但日志早就停了，
    通常意味着进程卡死或落盘失败。
    """
    log_dir = os.environ.get("LOG_DIR", "logs")
    invisible = {"dir": log_dir, "visible": False, "writable": False, "last_write_age_minutes": None}
    result = invisible
    # 单检查项失败不得影响整页：默认值先给好，异常由 degrade_guard 记录后继续
    with degrade_guard("system_status.log_dir", detail="读取日志目录失败，该项显示「不可见」"):
        path = Path(log_dir)
        if path.is_dir():
            newest = 0.0
            for entry in os.scandir(path):
                if entry.is_file():
                    newest = max(newest, entry.stat().st_mtime)
            result = {
                "dir": log_dir,
                "visible": True,
                "writable": os.access(path, os.W_OK),
                "last_write_age_minutes": None if newest == 0 else round((time.time() - newest) / 60, 1),
            }
    return result


def _backup_status() -> dict:
    """备份目录：最近一份备份的时间与年龄。

    目录不可见时只标 `visible=False`（本地开发 / 容器未挂载 ./backups 属正常），
    **不产生告警** —— 用假告警消磨信任比不显示更糟。
    """
    backup_dir = settings.BACKUP_DIR
    invisible = {"dir": backup_dir, "visible": False, "latest": None, "age_hours": None, "stale": None}
    result = invisible
    with degrade_guard("system_status.backup_dir", detail="读取备份目录失败，该项显示「不可见」"):
        path = Path(backup_dir)
        if path.is_dir():
            newest_file = None
            newest_ts = 0.0
            for entry in os.scandir(path):
                if entry.is_file() and entry.name.endswith(".sql.gz"):
                    ts = entry.stat().st_mtime
                    if ts > newest_ts:
                        newest_ts, newest_file = ts, entry.name
            if newest_file is None:
                # 目录在、但一份备份都没有：这是真问题（自动备份可能从未成功过）
                result = {"dir": backup_dir, "visible": True, "latest": None, "age_hours": None, "stale": True}
            else:
                age_hours = round((time.time() - newest_ts) / 3600, 1)
                result = {
                    "dir": backup_dir,
                    "visible": True,
                    "latest": newest_file,
                    "age_hours": age_hours,
                    "stale": age_hours >= max(1, settings.BACKUP_STALE_HOURS),
                }
    return result


def alert_readiness() -> dict:
    """告警与推送通道的就绪度：配了没配，比「有没有这个功能」更值得显示。

    这几项全部是「不配也不报错，只是静默不生效」的配置 ——
    正是最容易被忘掉的一类。后台把它们显式列出来，才能提醒人去填。
    """
    return {
        "sentry": bool((settings.SENTRY_DSN or "").strip()),
        "watchdog_webhook": bool((settings.WATCHDOG_WEBHOOK or "").strip()),
        "baidu_push": bool((settings.BAIDU_PUSH_TOKEN or "").strip()),
        "indexnow": bool((settings.INDEXNOW_KEY or "").strip()),
    }


async def _read_json_key(key: str) -> dict | None:
    """读 Redis 里的 JSON 值；缺失 / 不可用 / 格式坏一律返回 None"""
    try:
        raw = await redis_client.get(key)
    except Exception as exc:  # noqa: BLE001
        log_redis_degraded("system_status._read_json_key", f"读取 {key} 失败: {exc}")
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"system_status: {key} 内容不是合法 JSON，已忽略")
        return None
    return value if isinstance(value, dict) else None


async def read_json(key: str) -> dict | None:
    """``_read_json_key`` 的公开入口（供定时任务管理服务复用同一套容错逻辑）"""
    return await _read_json_key(key)


async def get_jobs_status() -> list[dict]:
    """定时任务：最近一次执行时间、是否成功、是否超期未跑、是否被停用。

    停用状态的来源是调度器写回的 `ops:jobs:applied`（它记着「我按哪份配置在跑」），
    而不是数据库：本函数刻意不碰 DB —— `get_system_status` 要在数据库故障时也能回答
    「哪里坏了」，一旦依赖 DB 就会在最需要它的时候一起挂掉。
    """
    now = int(time.time())
    applied = await _read_json_key(scheduled_job_service.APPLIED_KEY)
    applied_jobs = (applied or {}).get("jobs") or {}

    items: list[dict] = []
    for job_id, label in JOBS:
        meta = applied_jobs.get(job_id)
        # 没有 applied 标记（调度器没起）时按「启用」算：宁可报「未按时执行」，
        # 也不要因为读不到配置就把一屏任务静默成「已停用」
        enabled = not (isinstance(meta, dict) and meta.get("enabled") is False)

        raw = await _read_json_key(f"{_JOB_KEY_PREFIX}{job_id}")
        if raw is None:
            items.append(
                {
                    "id": job_id,
                    "label": label,
                    "enabled": enabled,
                    "last_run_at": None,
                    "ok": None,
                    "detail": "",
                    "stale": enabled,
                    "stale_reason": "无执行记录",
                }
            )
            continue
        at = int(raw.get("at") or 0)
        age = now - at if at else None
        limit = _JOB_STALE_SECONDS.get(job_id, 26 * 3600)
        stale = enabled and (age is None or age > limit)
        items.append(
            {
                "id": job_id,
                "label": label,
                "enabled": enabled,
                "last_run_at": at or None,
                "age_seconds": age,
                "ok": raw.get("ok"),
                "detail": raw.get("detail") or "",
                "stale": stale,
                "stale_reason": "" if not stale else f"已 {round((age or 0) / 3600, 1)} 小时未执行",
            }
        )
    return items


# 待处理项的类别：前端按类别做筛选（「10 条一模一样的黄色提示」是看板最容易让人迷失的地方，
# 分成「依赖故障 / 配置缺失 / 定时任务 / 数据巡检 / 备份」后才能各看各的）
WARNING_CATEGORY_LABELS: dict[str, str] = {
    "dependency": "依赖与版本",
    "config": "配置缺失",
    "job": "定时任务",
    "data": "数据巡检",
    "backup": "备份",
}


def _warn(level: str, category: str, text: str) -> dict:
    """构造一条待处理项（类别文案由后端下发，避免前后端各维护一份映射）"""
    return {"level": level, "category": category, "category_label": WARNING_CATEGORY_LABELS[category], "text": text}


def _collect_warnings(status: dict) -> list[dict]:
    """把「需要人处理」的项汇总成一句句人话（前端直接按顺序展示）"""
    warnings: list[dict] = []
    checks = status.get("checks") or {}

    mysql = checks.get("mysql") or {}
    if mysql.get("ok") is False:
        warnings.append(_warn("error", "dependency", f"MySQL 探测失败：{mysql.get('error') or '未知原因'}"))
    redis = checks.get("redis") or {}
    if redis.get("ok") is False:
        warnings.append(_warn("error", "dependency", f"Redis 探测失败：{redis.get('error') or '未知原因'}"))

    schema = checks.get("schema") or {}
    if schema.get("in_sync") is False:
        warnings.append(
            _warn(
                "error",
                "dependency",
                f"数据库版本与代码不一致（库 {schema.get('db_revision')} / 代码 {schema.get('code_head')}），"
                "新功能会报 Unknown column",
            )
        )

    backup = checks.get("backup") or {}
    if backup.get("visible") and backup.get("stale"):
        age = backup.get("age_hours")
        warnings.append(
            _warn(
                "warn",
                "backup",
                "最近备份为空" if age is None else f"最近一份备份已 {age} 小时（阈值 {settings.BACKUP_STALE_HOURS}h）",
            )
        )

    alerts = checks.get("alerts") or {}
    if not alerts.get("sentry"):
        warnings.append(_warn("warn", "config", "未配置 SENTRY_DSN：线上异常不会自动上报"))
    if not alerts.get("watchdog_webhook"):
        warnings.append(
            _warn("warn", "config", "未配置 WATCHDOG_WEBHOOK：看门狗只写本地日志，出事不会有人收到通知")
        )

    for job in status.get("jobs") or []:
        # 已停用是**人主动关的**，不该出现在「需要处理」里 —— 停用本身要显性展示，
        # 但不能变成一条天天挂着的告警（否则真故障会淹没在「你关掉的那个任务」下面）
        if not job.get("enabled", True):
            continue
        if job.get("stale"):
            warnings.append(
                _warn("warn", "job", f"定时任务「{job['label']}」{job.get('stale_reason') or '未按时执行'}")
            )
        elif job.get("ok") is False:
            warnings.append(_warn("warn", "job", f"定时任务「{job['label']}」上次执行失败"))

    quality = checks.get("data_quality") or {}
    if quality.get("issue_total"):
        warnings.append(_warn("warn", "data", f"数据巡检发现 {quality['issue_total']} 项异常（见下方明细）"))

    return warnings


async def get_system_status() -> dict:
    """系统状态聚合结果（带短缓存）。

    缓存失败/不可用一律退化为实时聚合，判定结果不受影响。
    """
    ttl = max(0, settings.SYSTEM_STATUS_CACHE_SECONDS)
    if ttl:
        cached = await _read_json_key(_CACHE_KEY)
        if cached:
            return cached

    # 1) 依赖与库版本：复用深度探针（自带 5s 缓存，失败也只是标记 ok=False）
    # 探针本身炸了也要给出可读结论 —— 默认值先给好，异常由 degrade_guard 记录后继续
    deep = {"status": "degraded", "checks": {}}
    with degrade_guard("system_status.deep_probe", detail="深度探针异常，依赖结论标「未知」"):
        deep, _ = await health_service.get_deep_health()

    deep_checks = deep.get("checks") or {}
    mysql = deep_checks.get("mysql") or {"ok": None, "error": "未知"}
    redis = deep_checks.get("redis") or {"ok": None, "error": "未知"}

    status: dict = {
        "ts": int(time.time()),
        "checks": {
            "mysql": mysql,
            "redis": redis,
            "schema": {
                "code_head": mysql.get("schema_head"),
                "db_revision": mysql.get("schema_revision"),
                # 任一侧读不到 → None（未知），不谎报「一致」
                "in_sync": (
                    None
                    if mysql.get("schema_head") is None or mysql.get("schema_revision") is None
                    else mysql.get("schema_head") == mysql.get("schema_revision")
                ),
            },
            "logs": _log_dir_status(),
            "backup": _backup_status(),
            "alerts": alert_readiness(),
            "data_quality": await _read_json_key(_QUALITY_KEY) or {},
        },
        "jobs": await get_jobs_status(),
        "env": {
            "app_env": settings.APP_ENV,
            "release": settings.APP_RELEASE or None,
            "monitoring_enabled": bool((settings.SENTRY_DSN or "").strip()),
        },
    }
    status["warnings"] = _collect_warnings(status)

    if ttl:
        try:
            await redis_client.set(_CACHE_KEY, json.dumps(status), ex=ttl)
        except Exception as exc:  # noqa: BLE001 缓存写失败不影响本次返回
            log_redis_degraded("system_status.get_system_status", f"状态缓存写入失败: {exc}")

    return status
