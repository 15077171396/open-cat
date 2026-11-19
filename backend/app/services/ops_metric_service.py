"""核心指标快照服务：每小时采集一帧系统读数，供看板回看趋势。

为什么是快照而不是现算：看板已有「内容/注册趋势」（按 created_at 现算），
但总量增长、待办积压、登录失败数这类读数要么跨多张表、要么带窗口语义
（近 1 小时登录失败），现算慢且无法回看历史水位。快照把读数钉在时间轴上，
加指标也**不用改表结构**（metrics 是 JSON，历史行缺键前端按缺失展示）。

写入侧：`take_snapshot` 只 add + 顺带清理过期行，**不 commit**——由调度器
任务体（scheduler/tasks.py::ops_metric_snapshot）持有会话并提交，
与审计/搜索日志清理同一事务约定。读取侧查询失败整体降级为空趋势
（快照表还没建时页面显示「尚未采集」，不 500）。

保留期 180 天（写入前顺带清理，见 OPS_SNAPSHOT_RETENTION_DAYS）。
"""

from __future__ import annotations

from datetime import timedelta

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.models.comment import ContentComment
from app.models.feedback import Feedback
from app.models.login_log import LoginLog
from app.models.moderation import ContentModerationLog
from app.models.ops_metric_snapshot import OPS_SNAPSHOT_RETENTION_DAYS, OpsMetricSnapshot
from app.models.tutorial import Tutorial
from app.models.user import User
from app.utils.time import utcnow

# 指标目录：键 → 展示名与语义方向。**新增指标只加一行**（历史快照缺键，前端按缺失处理）；
# 展示名放后端保证写入端与界面同源（与审核模块 CONTENT_TYPE_LABELS 同一理由）。
# good_when_up：数值上升是否是「好事」—— 决定环比箭头的分色
# （总量类上升是增长=绿；积压/失败类上升是恶化=红）。
METRIC_DEFS: dict[str, dict[str, object]] = {
    "users_total": {"label": "注册用户总量", "good_when_up": True},
    "tutorials_total": {"label": "教程总量", "good_when_up": True},
    "comments_total": {"label": "评论总量", "good_when_up": True},
    "feedbacks_pending": {"label": "待处理反馈", "good_when_up": False},
    "moderation_pending": {"label": "待复核机审", "good_when_up": False},
    "login_failures_1h": {"label": "近1小时登录失败", "good_when_up": False},
}

# 兼容旧调用点（tests / 端点 metric_options）：键 → 展示名
METRIC_LABELS: dict[str, str] = {key: str(defn["label"]) for key, defn in METRIC_DEFS.items()}

# 趋势查询允许的窗口（小时）：2~720（30 天）
_MIN_HOURS = 2
_MAX_HOURS = 720


async def collect_metrics(db: AsyncSession) -> dict[str, int]:
    """采集一帧指标。单项失败降级为 0 并记日志（快照宁可缺一项也别整帧缺失）。"""
    metrics: dict[str, int] = {}

    async def _grab(key: str, query) -> None:
        # 先落默认值：degrade_guard 吞掉异常后这行不执行会让键整个缺失（趋势断线），
        # 而不是想要的「该项按 0 记」
        metrics[key] = 0
        with degrade_guard("ops_metric.collect", detail=f"指标 {key} 采集失败，按 0 记"):
            metrics[key] = int((await db.execute(query)).scalar() or 0)

    since_1h = utcnow().replace(tzinfo=None) - timedelta(hours=1)
    await _grab("users_total", select(func.count(User.id)).where(User.deleted_at.is_(None)))
    await _grab(
        "tutorials_total",
        select(func.count(Tutorial.id)).where(Tutorial.deleted_at.is_(None)),
    )
    await _grab(
        "comments_total",
        select(func.count(ContentComment.id)).where(ContentComment.deleted_at.is_(None)),
    )
    await _grab(
        "feedbacks_pending",
        select(func.count(Feedback.id)).where(Feedback.status == "pending"),
    )
    await _grab(
        "moderation_pending",
        select(func.count(ContentModerationLog.id)).where(
            ContentModerationLog.status == "pending"
        ),
    )
    await _grab(
        "login_failures_1h",
        select(func.count(LoginLog.id)).where(
            LoginLog.status == "failed",
            LoginLog.created_at >= since_1h,
        ),
    )
    return metrics


async def take_snapshot(db: AsyncSession) -> dict[str, int]:
    """采集并写入一帧快照（含过期行清理；**执行不提交**，由调用方 commit）。"""
    metrics = await collect_metrics(db)
    db.add(OpsMetricSnapshot(metrics=metrics))
    cutoff = utcnow().replace(tzinfo=None) - timedelta(days=OPS_SNAPSHOT_RETENTION_DAYS)
    await db.execute(
        text("DELETE FROM ops_metric_snapshots WHERE taken_at < :cutoff"), {"cutoff": cutoff}
    )
    logger.info(f"核心指标快照已采集: {metrics}")
    return metrics


async def latest_summary(db: AsyncSession) -> dict:
    """最近两帧快照的全指标对比（看板「多指标概览卡」的数据源）。

    value = 最新帧读数；previous = 上一帧读数；delta = value - previous
    （只有一帧时 previous/delta 为 None —— 新部署刚跑完第一次快照的状态）。
    任一读取失败 → available=False（不 500，观测页降级）。
    """
    frames: list[OpsMetricSnapshot] = []
    with degrade_guard("ops_metric.summary", detail="指标概览读取失败（可能是迁移未执行）"):
        frames = (
            await db.execute(
                select(OpsMetricSnapshot)
                .order_by(OpsMetricSnapshot.taken_at.desc(), OpsMetricSnapshot.id.desc())
                .limit(2)
            )
        ).scalars().all()

    if not frames:
        return {"available": False, "frame_count": 0, "taken_at": None, "items": []}

    current = frames[0].metrics or {}
    previous = frames[1].metrics if len(frames) >= 2 else {}

    items: list[dict] = []
    for key, defn in METRIC_DEFS.items():
        value = (current or {}).get(key)
        prev = (previous or {}).get(key)
        delta = None
        if value is not None and prev is not None:
            delta = int(value) - int(prev)
        items.append(
            {
                "key": key,
                "label": str(defn["label"]),
                "good_when_up": bool(defn["good_when_up"]),
                "value": int(value) if value is not None else None,
                "previous": int(prev) if prev is not None else None,
                "delta": delta,
            }
        )

    return {
        "available": bool(frames),
        "frame_count": len(frames),
        "taken_at": frames[0].taken_at.isoformat() if frames else None,
        "items": items,
    }


async def trend(db: AsyncSession, metric: str, hours: int = 48) -> dict:
    """取某指标近 N 小时的趋势点（时间升序）。

    任一查询失败（含快照表未建）→ available=False 的空趋势，不抛异常。
    """
    if metric not in METRIC_LABELS:
        raise AppException(code=400, message="指标键无效")
    window = max(_MIN_HOURS, min(_MAX_HOURS, int(hours)))
    since = utcnow().replace(tzinfo=None) - timedelta(hours=window)

    points: list[dict] = []
    with degrade_guard("ops_metric.trend", detail="指标趋势查询失败（可能是迁移未执行）"):
        rows = (
            await db.execute(
                select(OpsMetricSnapshot)
                .where(OpsMetricSnapshot.taken_at >= since)
                .order_by(OpsMetricSnapshot.taken_at.asc(), OpsMetricSnapshot.id.asc())
            )
        ).scalars().all()
        for row in rows:
            value = (row.metrics or {}).get(metric)
            if value is None:
                continue
            points.append({
                "taken_at": row.taken_at.isoformat() if row.taken_at else None,
                "count": int(value),
            })

    return {
        "metric": metric,
        "label": METRIC_LABELS[metric],
        "hours": window,
        "available": bool(points),
        "points": points,
    }
