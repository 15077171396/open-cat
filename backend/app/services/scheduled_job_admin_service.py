"""定时任务的可管理面：启停 / 改周期 / 立即执行一次。

三个入口，都是**写「期望状态」**，不直接操作调度器：
- ``list_jobs(db)``：目录 + 库配置 + Redis 心跳 + 调度器生效状态 → 页面载荷；
- ``update_job(...)``：写配置表 + 审计，等调度器 ≤5 秒对齐后生效；
- ``trigger_run(...)``：往命令队列塞一条，调度器取到就执行一次。

为什么不能在这里直接调 handler：
    handler 里有 DB 会话、外部 API 调用与并发假设，且调度器容器是**单实例**执行模型。
    在 API 进程（多 worker）里直接跑会绕过 max_instances、心跳与容器隔离，
    出现「两条命令并发清理同一批日志」这种问题。跨进程只走队列，执行权留给调度器。

三条「宁可不做也不静默」的取舍：
1. **调度器没在跑时拒绝下发**（503）。塞进队列看着成功、实际永远不会被执行，
   比直接报错恶劣得多 —— 用户会以为「已经手动跑过了」。
2. **同一任务 10 秒冷却**（429），避免连点把队列堆满。
3. **配置没变就不写库、不写审计**：前端重复保存同一份配置不应产生噪音审计。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.degrade import degrade_closed, degrade_guard
from app.core.exceptions import AppException, NotFoundError
from app.models.scheduled_job_config import ScheduledJobConfig
from app.models.user import User
from app.redis import redis_client
from app.services import scheduled_job_service as sjs
from app.services import system_status_service as sss
from app.services.admin_audit_service import record_admin_action

# 同一任务的手动触发冷却（秒）。比「立即执行」的实际耗时短得多，
# 目的只是拦住连点，不是限流策略 —— 真正的重复执行由调度器的内存闩锁挡住。
RUN_COOLDOWN_SECONDS = 10


def _find_spec(job_id: str) -> sjs.JobSpec:
    for spec in sjs.JOB_SPECS:
        if spec.id == job_id:
            return spec
    raise NotFoundError(f"未知的定时任务: {job_id}")


async def load_configs(db: AsyncSession) -> tuple[dict[str, sjs.JobConfig], bool]:
    """读全部配置；返回 (配置, 表是否可用)。

    表不存在（迁移未执行）时**不抛**：页面照常打开，但会明确提示「配置表不可用」，
    此时调度器也按代码默认值在跑 —— 让运维看得见「现在是默认状态」，而不是一个 500。
    """
    try:
        rows = (await db.execute(select(ScheduledJobConfig))).scalars().all()
    except SQLAlchemyError as exc:
        logger.warning(f"读取定时任务配置失败（按代码默认值展示）: {exc}")
        return dict(sjs.DEFAULT_CONFIGS), False

    by_id = {row.job_id: row for row in rows}
    configs = {
        spec.id: sjs.config_from_row(spec, sjs.config_view(by_id.get(spec.id)))
        for spec in sjs.JOB_SPECS
    }
    return configs, True


def _configured_flags() -> dict[str, bool]:
    return {
        "BAIDU_PUSH_TOKEN": bool((settings.BAIDU_PUSH_TOKEN or "").strip()),
        "INDEXNOW_KEY": bool((settings.INDEXNOW_KEY or "").strip()),
    }


async def _read_heartbeats() -> dict[str, dict | None]:
    return {
        spec.id: await sss.read_json(f"{sss.HEARTBEAT_KEY_PREFIX}{spec.id}")
        for spec in sjs.JOB_SPECS
    }


async def list_jobs(db: AsyncSession) -> dict:
    """页面载荷（只读，不写任何东西）"""
    configs, available = await load_configs(db)
    overview = sjs.build_overview(
        await _read_heartbeats(),
        time.time(),
        _configured_flags(),
        configs,
        await sss.read_json(sjs.APPLIED_KEY),
    )
    overview["configs_available"] = available
    # 冷却状态：让页面把按钮禁用掉，而不是点了才收到 429
    overview["run_cooldown_seconds"] = RUN_COOLDOWN_SECONDS
    return overview


async def update_job(
    db: AsyncSession,
    admin: User,
    job_id: str,
    *,
    enabled: bool | None = None,
    schedule_kind: str | None = None,
    hour: int | None = None,
    minute: int | None = None,
    interval_minutes: int | None = None,
) -> dict:
    """更新配置（部分更新：未传的字段保持原值）。

    校验全部在这里做，而不是只靠 pydantic 的字段范围 —— 「daily 却传了
    interval_minutes」「interval 却漏了间隔」这类**组合错误** pydantic 表达不了。
    """
    spec = _find_spec(job_id)
    configs, _ = await load_configs(db)
    current = configs[spec.id]

    kind = schedule_kind or current.kind
    if kind not in ("daily", "interval"):
        raise AppException(code=400, message="调度类型只能是 daily 或 interval")

    if kind == "daily":
        new_hour = current.hour if hour is None else hour
        new_minute = current.minute if minute is None else minute
        if not (0 <= new_hour <= 23 and 0 <= new_minute <= 59):
            raise AppException(code=400, message="每日执行时间必须是 00:00 - 23:59")
        new_interval = current.interval_minutes
    else:
        new_interval = current.interval_minutes if interval_minutes is None else interval_minutes
        if not (1 <= new_interval <= 10080):
            raise AppException(code=400, message="执行间隔必须在 1 - 10080 分钟（7 天）之间")
        new_hour, new_minute = current.hour, current.minute

    updated = sjs.JobConfig(
        enabled=current.enabled if enabled is None else bool(enabled),
        kind=kind,
        hour=new_hour,
        minute=new_minute,
        interval_minutes=new_interval,
    )

    if updated == current:
        # 没变化就不写库、不写审计：前端重复保存不该制造噪音
        return {
            "job_id": job_id,
            "changed": False,
            "enabled": updated.enabled,
            "schedule": updated.schedule_text,
        }

    row = await db.get(ScheduledJobConfig, job_id)
    if row is None:
        row = ScheduledJobConfig(job_id=job_id)
        db.add(row)
    row.enabled = updated.enabled
    row.schedule_kind = updated.kind
    # 与调度类型无关的列一律清空：残留值会让「库里的数据」和「生效的周期」对不上，
    # 下次排查时会怀疑是不是配置没保存成功
    row.hour = updated.hour if updated.kind == "daily" else None
    row.minute = updated.minute if updated.kind == "daily" else None
    row.interval_minutes = updated.interval_minutes if updated.kind == "interval" else None
    row.updated_by = (getattr(admin, "account", "") or "")[:120]
    row.updated_at = datetime.now(timezone.utc)

    summary = f"「{spec.label}」{current.schedule_text}{'' if current.enabled else '（停用）'} → " \
              f"{updated.schedule_text}{'' if updated.enabled else '（停用）'}"
    record_admin_action(
        db,
        admin,
        "scheduled_job_update",
        target_type="scheduled_job",
        summary=summary,
        # detail 只放配置项，不放任何 secret（本表本来也没有）
        detail={
            "job_id": job_id,
            "from": {"enabled": current.enabled, **current.as_dict()},
            "to": {"enabled": updated.enabled, **updated.as_dict()},
        },
    )
    await db.commit()

    logger.info(f"定时任务配置已更新: {summary}（操作者 {getattr(admin, 'account', '')}）")
    return {
        "job_id": job_id,
        "changed": True,
        "enabled": updated.enabled,
        "schedule": updated.schedule_text,
    }


async def trigger_run(db: AsyncSession, admin: User, job_id: str) -> dict:
    """立即执行一次：把命令塞进队列，由调度器容器执行。"""
    spec = _find_spec(job_id)

    applied = await sss.read_json(sjs.APPLIED_KEY)
    if not sjs.scheduler_alive(applied, time.time()):
        # 明确报错而不是「塞进去等它自己跑」：队列里的命令不会有人消费
        raise AppException(
            code=503,
            message="调度器未运行（最近 60 秒没有心跳），命令无法下发；请先检查 scheduler 容器",
        )

    cooldown_key = f"ops:jobs:cooldown:{job_id}"
    allowed = True
    # 冷却只是防抖：Redis 抖动时放行（真正的不可用会在下面入队时明确报错）
    with degrade_guard("scheduled_job.cooldown", detail="冷却标记写入失败，本次放行"):
        allowed = bool(await redis_client.set(cooldown_key, "1", nx=True, ex=RUN_COOLDOWN_SECONDS))
    if not allowed:
        raise AppException(code=429, message=f"刚刚已触发过「{spec.label}」，请 {RUN_COOLDOWN_SECONDS} 秒后再试")

    payload = json.dumps(
        {"job_id": job_id, "at": int(time.time()), "by": (getattr(admin, "account", "") or "")[:120]},
        ensure_ascii=False,
    )
    try:
        await redis_client.lpush(sjs.COMMAND_QUEUE_KEY, payload)
        # 只保留最近 N 条：命令队列不该无限增长（正常一次点击一条）
        await redis_client.ltrim(sjs.COMMAND_QUEUE_KEY, 0, sjs.COMMAND_QUEUE_MAX - 1)
    except Exception as exc:  # noqa: BLE001 必须让调用方知道「没下发成功」，不能静默
        degrade_closed(
            "scheduled_job.enqueue",
            exc,
            status=503,
            message="命令队列不可用（Redis 异常），请稍后重试",
        )

    # 审计写在入队**之后**：先记「已触发」再入队，一旦入队失败就会留下一条假审计
    record_admin_action(
        db,
        admin,
        "scheduled_job_run",
        target_type="scheduled_job",
        summary=f"手动执行「{spec.label}」",
        detail={"job_id": job_id, "queued_at": int(time.time())},
    )
    await db.commit()

    logger.info(f"定时任务手动触发已入队: {job_id}（操作者 {getattr(admin, 'account', '')}）")
    return {"job_id": job_id, "queued": True, "cooldown_seconds": RUN_COOLDOWN_SECONDS}
