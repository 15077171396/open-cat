"""接口调用统计查询服务：小时桶聚合 + 慢请求明细（供「接口监控」页消费）。

读取侧与写入侧（core/api_stats.py）共用同一组 Redis 键。查询失败整体降级为
`available=False` 的空报告（观测页不 500）；开关关闭时同样返回 available=False，
前端显示「尚未采集」而不是一片空白让人以为没有流量。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401  保持与其它查询服务同签名形态

from app.config import settings
from app.core.api_stats import SLOW_KEY, STATS_KEY_PREFIX
from app.core.degrade import degrade_guard
from app.redis import redis_client

# Top 榜的最低样本量：错误率统计里 <5 次调用的路径不参与排序（1/1=100% 会霸榜）
_MIN_SAMPLES_FOR_ERROR_RATE = 5


def _bucket_keys(now: datetime) -> list[str]:
    """当前小时 + 上一小时的桶键（保留两桶足够回看）"""
    current = now.strftime("%Y%m%d%H")
    previous = (now - timedelta(hours=1)).strftime("%Y%m%d%H")
    return [f"{STATS_KEY_PREFIX}{current}", f"{STATS_KEY_PREFIX}{previous}"]


async def get_overview(limit: int = 20) -> dict:
    """接口统计总览：调用量/错误率 Top + 慢接口明细。

    任一读取失败（含 Redis 不可用、开关关闭）→ available=False，不抛异常。
    """
    available = True
    stats: dict[str, dict[str, int]] = {}
    slow: list[dict] = []

    if settings.API_STATS_ENABLED:
        now = datetime.now(timezone.utc)
        with degrade_guard("api_stats.overview", detail="接口统计读取失败（Redis 不可用？）"):
            fields = await redis_client.hgetall(_bucket_keys(now)[0])
            for field, count in (fields or {}).items():
                parts = str(field).split(" ", 2)
                if len(parts) != 3:
                    continue
                method, path, bucket = parts
                entry = stats.setdefault(f"{method} {path}", {"calls": 0, "4xx": 0, "5xx": 0})
                entry["calls"] += int(count)
                if bucket in ("4xx", "5xx"):
                    entry[bucket] += int(count)

            raw_items = await redis_client.lrange(SLOW_KEY, 0, limit - 1)
            for raw in raw_items:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(payload, dict):
                    slow.append(payload)
    else:
        available = False

    ranked = sorted(stats.items(), key=lambda kv: kv[1]["calls"], reverse=True)
    top_calls = [
        {"key": key, **entry, "error_rate": _error_rate(entry)}
        for key, entry in ranked[:limit]
        if entry["calls"] >= _MIN_SAMPLES_FOR_ERROR_RATE
    ]
    top_errors = sorted(
        (item for item in top_calls if item["5xx"] > 0),
        key=lambda item: item["5xx"],
        reverse=True,
    )[:limit]

    return {
        "available": available,
        "enabled": settings.API_STATS_ENABLED,
        "slow_threshold_ms": settings.API_SLOW_MS,
        "min_samples": _MIN_SAMPLES_FOR_ERROR_RATE,
        "unique_paths": len(stats),
        "total_calls": sum(entry["calls"] for entry in stats.values()),
        "top_calls": top_calls,
        "top_errors": top_errors,
        "slow_requests": slow,
    }


def _error_rate(entry: dict[str, int]) -> float | None:
    total = entry["calls"]
    errors = entry["4xx"] + entry["5xx"]
    if not total:
        return None
    return round(errors / total, 4)
