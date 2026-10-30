"""监控数据：全站登录会话、宿主机资源、Redis 缓存。

三类数据都是**观测数据**：失败时必须降级为「不可用」而不是 500，
否则一个 Redis 抖动会把「服务器监控」也带挂。宽泛捕获统一走 `degrade_guard`，
不新增裸宽泛捕获（`test_degrade_ratchet` 只许减不许增）。
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.core.session_store import SESSION_PREFIX, describe_user_agent
from app.models.user import User
from app.redis import redis_client

MAX_SCAN_KEYS = 5000


def _server_stats_sync() -> dict:
    try:
        import psutil
    except ImportError:
        return {"available": False, "error": "psutil 未安装，无法读取服务器资源"}

    import time

    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    process = psutil.Process(os.getpid())
    boot = int(psutil.boot_time())
    try:
        load_avg = [round(value, 2) for value in psutil.getloadavg()]
    except (AttributeError, OSError):
        load_avg = None
    return {
        "available": True,
        "platform": f"{platform.system()} {platform.release()}",
        "python_version": platform.python_version(),
        "cpu_count": psutil.cpu_count(logical=True) or 0,
        "cpu_percent": round(cpu_percent, 1),
        "load_avg": load_avg,
        "memory_total": int(memory.total),
        "memory_used": int(memory.used),
        "memory_percent": round(memory.percent, 1),
        "swap_total": int(swap.total),
        "swap_used": int(swap.used),
        "swap_percent": round(swap.percent, 1),
        "disk_total": int(disk.total),
        "disk_used": int(disk.used),
        "disk_percent": round(disk.percent, 1),
        "boot_time": boot,
        "uptime_seconds": max(0, int(time.time()) - boot),
        "process_pid": process.pid,
        "process_memory": int(process.memory_info().rss),
        "process_threads": process.num_threads(),
    }


async def get_server_stats() -> dict:
    result: dict = {"available": False, "error": "读取系统指标失败"}
    with degrade_guard("monitor.server", detail="宿主机指标读取失败，页面显示不可用"):
        result = await asyncio.to_thread(_server_stats_sync)
    return result


async def get_cache_stats() -> dict:
    result: dict = {"available": False, "error": "Redis 不可用"}
    with degrade_guard("monitor.cache", detail="Redis 指标读取失败，页面显示不可用"):
        info = await redis_client.info()
        total_keys = int(await redis_client.dbsize() or 0)
        prefixes: Counter[str] = Counter()
        scanned = 0
        truncated = False
        async for key in redis_client.scan_iter(match="*", count=500):
            scanned += 1
            if scanned > MAX_SCAN_KEYS:
                truncated = True
                break
            name = str(key)
            prefix = f"{name.split(':', 1)[0]}:" if ":" in name else name
            prefixes[prefix] += 1
        hits = int(info.get("keyspace_hits") or 0)
        misses = int(info.get("keyspace_misses") or 0)
        requests = hits + misses
        result = {
            "available": True,
            "version": str(info.get("redis_version") or ""),
            "used_memory": int(info.get("used_memory") or 0),
            "used_memory_human": str(info.get("used_memory_human") or ""),
            "peak_memory_human": str(info.get("used_memory_peak_human") or ""),
            "maxmemory_human": str(info.get("maxmemory_human") or "未限制"),
            "connected_clients": int(info.get("connected_clients") or 0),
            "total_keys": total_keys,
            "hits": hits,
            "misses": misses,
            "hit_rate": round((hits / requests) * 100, 1) if requests else 0,
            "uptime_seconds": int(info.get("uptime_in_seconds") or 0),
            "scanned": min(scanned, MAX_SCAN_KEYS),
            "truncated": truncated,
            "prefixes": [
                {"prefix": prefix, "count": count}
                for prefix, count in prefixes.most_common(12)
            ],
        }
    return result


async def list_online_sessions(
    db: AsyncSession,
    *,
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """扫描会话簿，返回全站登录会话（最多扫描 MAX_SCAN_KEYS 个 key）。"""
    result: dict = {
        "available": False,
        "items": [],
        "total": 0,
        "unique_users": 0,
        "page": page,
        "page_size": page_size,
        "scanned": 0,
    }

    with degrade_guard("monitor.online", detail="在线会话读取失败，页面显示不可用"):
        keys: list[str] = []
        async for key in redis_client.scan_iter(match=f"{SESSION_PREFIX}*", count=500):
            keys.append(str(key))
            if len(keys) > MAX_SCAN_KEYS:
                break
        raws = await redis_client.mget(keys) if keys else []

        records: list[dict] = []
        for key, raw in zip(keys, raws):
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            uid = payload.get("uid")
            if uid is None:
                continue
            ua = payload.get("ua") or ""
            records.append(
                {
                    "sid": key[len(SESSION_PREFIX):],
                    "uid": int(uid),
                    "account": "",
                    "nickname": None,
                    "role": "",
                    "device": describe_user_agent(ua),
                    "ip": payload.get("ip") or "",
                    "created_at": int(payload.get("created_at") or 0),
                    "last_seen": int(payload.get("last_seen") or 0),
                }
            )

        uid_list = list({row["uid"] for row in records})
        users: dict[int, User] = {}
        if uid_list:
            user_rows = await db.execute(select(User).where(User.id.in_(uid_list)))
            users = {user.id: user for user in user_rows.scalars().all()}
        for row in records:
            user = users.get(row["uid"])
            if user is not None:
                row["account"] = user.account
                row["nickname"] = user.nickname
                row["role"] = user.role

        needle = keyword.strip().lower()
        if needle:
            records = [
                row
                for row in records
                if needle
                in " ".join(
                    [row["account"], row["nickname"] or "", row["device"], row["ip"], str(row["uid"])]
                ).lower()
            ]
        records.sort(key=lambda item: item["last_seen"], reverse=True)
        total = len(records)
        unique_users = len({row["uid"] for row in records})
        start = max(0, (page - 1) * page_size)
        result = {
            "available": True,
            "items": records[start : start + page_size],
            "total": total,
            "unique_users": unique_users,
            "page": page,
            "page_size": page_size,
            "scanned": min(len(keys), MAX_SCAN_KEYS),
        }
    return result
