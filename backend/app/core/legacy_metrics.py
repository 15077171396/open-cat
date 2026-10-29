"""兼容分支的可查询使用量：按 (事件, 自然日) Redis 计数保留 90 天，
供后台概览判断旧客户端是否还在发兼容请求、何时可摘除兼容分支；计数失败绝不影响业务。"""

from __future__ import annotations

import datetime as dt

from loguru import logger

from app.redis import log_redis_degraded, redis_client

# 已登记的兼容事件。新增兼容分支必须同时登记到这里，否则只会被当成「未知事件」
# 打一条 warning 而永远不进看板。取值由 tests/test_legacy_metrics.py 静态守卫。
LEGACY_EVENTS = (
    "plugin_legacy_sort",   # GET /plugins?sort=hottest|rating（旧小程序包）
    "comment_legacy_param",  # GET/POST /comments?tutorial_id=（旧用户端）
    "admin_legacy_param",   # GET /admin/comments?tutorial_id=（旧管理端 bundle）
)

_RETENTION_DAYS = 90
_KEY_PREFIX = "legacy_usage"


def _day(now: dt.datetime) -> str:
    # naive-UTC 自然日：与全库时间戳口径一致（连接层 UTC 锁定）
    return now.strftime("%Y%m%d")


def legacy_key(event: str, now: dt.datetime) -> str:
    return f"{_KEY_PREFIX}:{event}:{_day(now)}"


async def record_legacy_usage(event: str, *, ttl_seconds: int = 90 * 86400) -> None:
    """给一条兼容分支 +1。失败只记日志，不抛异常。"""
    if event not in LEGACY_EVENTS:
        logger.warning(f"未登记的兼容事件被计数: {event}（请补进 LEGACY_EVENTS）")
    key = legacy_key(event, dt.datetime.now(dt.timezone.utc))
    try:
        # INCR + 首次 EXPIRE：不能每次刷新 TTL（高频键永不回收），也不能省 EXPIRE（没人清）
        created = await redis_client.incr(key)
        if created == 1:
            await redis_client.expire(key, ttl_seconds)
    except Exception as exc:  # noqa: BLE001 计数失败不该让一次正常请求失败
        log_redis_degraded("legacy_metrics.record", f"兼容分支计数失败（event={event}）：{exc}")


async def legacy_usage_snapshot(days: int = 7) -> dict[str, dict[str, int]]:
    """近 N 天每个兼容事件的 今天/昨天/N 天累计。Redis 不可用返回空 dict——
    调用方必须区分「没有数据」和「没人再用」（看板省略该字段而非显示 0）。"""
    if days < 1:
        return {}
    today = dt.datetime.now(dt.timezone.utc)
    events = list(LEGACY_EVENTS)
    # 一次性把「今天 / 昨天 / 近 days 天」的键拍平，回填时按同一顺序取，
    # 不做花哨的切片步长运算 —— 那种写法在事件数变化时会静默错位
    plan: list[tuple[str, int, str]] = []  # (event, bucket, key)
    for index, event in enumerate(events):
        plan.append((event, "today", legacy_key(event, today)))
        plan.append((event, "yesterday", legacy_key(event, today - dt.timedelta(days=1))))
        for offset in range(days):
            plan.append((event, "last_days", legacy_key(event, today - dt.timedelta(days=offset))))

    try:
        pipe = redis_client.pipeline()
        for _event, _bucket, key in plan:
            pipe.get(key)
        values = await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        log_redis_degraded("legacy_metrics.snapshot", f"兼容分支用量读取失败：{exc}")
        return {}

    out: dict[str, dict[str, int]] = {
        event: {"today": 0, "yesterday": 0, "last_days": 0} for event in events
    }
    for (event, bucket, _key), raw in zip(plan, values):
        count = int(raw or 0)
        if bucket == "last_days":
            out[event][bucket] += count
        else:
            out[event][bucket] = count
    return out
