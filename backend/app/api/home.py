"""首页聚合路由 (首屏一次请求聚合全部数据，7 个请求 → 1 个)"""

import asyncio
import contextlib
import json
import random
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from loguru import logger

from app.core.exceptions import success_response
from app.database import async_session
from app.redis import log_redis_degraded, redis_client
from app.schemas.common import ApiResponse
from app.services.stats_service import get_public_stats
from app.services.tutorial_service import get_tutorials
from app.services.node_service import list_nodes
from app.services.tool_service import get_tools
from app.services.resource_service import get_resources
from app.services.plugin_service import list_plugins
from app.services.category_service import list_categories_with_tutorial_count
from app.services.announcement_service import list_latest_announcements
from app.services.content_cache import home_aggregate_key
from app.schemas.admin import ActionAck

router = APIRouter()

# 键在调用时现算，不存成 import 期常量：常量会把形状版本冻结在导入那一刻，
# 与 content_cache 失效侧现算出的键名漂移（P1-73）
CACHE_TTL = 60  # 首屏数据允许短暂陈旧，缓存 60 秒显著降低 DB 压力

_EMPTY_PAGE = {"items": [], "total": 0}
_EMPTY_STATS = {"total_tutorials": 0, "total_resources": 0, "total_plugins": 0, "total_users": 0}

# 进程内 single-flight 锁：TTL 到期瞬间避免 N 个并发请求各自重复执行
# 8 个子查询（放大 8N 倍 DB 压力），拿到锁后二次检查缓存
_aggregate_lock = asyncio.Lock()

# 等锁上限：锁被慢请求长期占住时后续所有首页请求会无限期排队，把「一个板块慢」
# 放大成「全站不可用」；超时即放弃排他、直接查 DB
_AGGREGATE_LOCK_TIMEOUT = 5.0

# 单次聚合最多同时占用几条池连接：8 个子查询各开一条独立 Session，而池上限仅
# 15/worker；_single_flight 超时后每个放弃排他的请求还会各自扇出 8 条，3 个超时请求即打满
_AGGREGATE_MAX_CONCURRENT = 4
_aggregate_semaphore = asyncio.Semaphore(_AGGREGATE_MAX_CONCURRENT)

# 放弃排他时优先复用的「上一次成功结果」（stale-while-revalidate）。
# _STALE_MAX_AGE 刻意大于 CACHE_TTL：缓存过期正说明有人正在重建，
# 用一份略旧的首页数据顶 60~180 秒，比让首页把全站连接池抽干好得多。
_STALE_MAX_AGE = 180.0
_LAST_GOOD: tuple[float, dict] | None = None


@contextlib.asynccontextmanager
async def _single_flight(timeout: float = _AGGREGATE_LOCK_TIMEOUT):
    """尽力获取 single-flight 锁；等不到就放弃排他（不阻塞、不报错）。"""
    try:
        await asyncio.wait_for(_aggregate_lock.acquire(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            f"首页聚合等锁超时（{timeout}s），本次放弃 single-flight 直查 DB"
            "（若持续出现，说明有慢查询占住聚合锁）"
        )
        yield False
        return
    try:
        yield True
    finally:
        _aggregate_lock.release()


def _ttl_with_jitter(base: int) -> int:
    """给固定 TTL 加抖动：固定 TTL 会让「每分钟同一毫秒」集体过期造成缓存击穿尖峰"""
    return base + random.randint(0, max(1, base // 4))


async def _cache_get(key: str) -> str | None:
    """缓存读取（Redis 故障降级返回 None，直查 DB 而非 500）"""
    try:
        return await redis_client.get(key)
    except Exception:
        log_redis_degraded("home._cache_get", "Redis 不可用，首页聚合缓存降级直查 DB")
        return None


async def _cache_set(key: str, ttl: int, value: str) -> None:
    """缓存写入（Redis 故障静默跳过）"""
    try:
        await redis_client.set(key, value, ex=ttl)
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("home._cache_set", exc)


async def _run_with_session(fn: Callable[[Any], Awaitable[Any]]) -> Any:
    """为子查询分配独立 Session 并兜底异常（单板块失败不影响整页）；经信号量限并发，不再同时抢 8 条池连接"""
    async with _aggregate_semaphore:
        try:
            async with async_session() as session:
                return await fn(session)
        except Exception:
            logger.exception("home aggregate sub-query failed")
            return None


async def _cached_payload() -> dict | None:
    """读聚合缓存；值损坏时删除并回退重建（首屏与单飞锁内双重检查共用，避免两处判定漂移）"""
    cached = await _cache_get(home_aggregate_key())
    if not cached:
        return None
    try:
        return json.loads(cached)
    except (ValueError, TypeError):
        logger.warning("首页聚合缓存损坏，删除后重建")
        try:
            await redis_client.delete(home_aggregate_key())
        except Exception as exc:  # noqa: BLE001 降级不阻断主流程
            log_redis_degraded("home._cached_payload", exc)
        return None


@router.get("", response_model=ApiResponse[ActionAck])
async def home_aggregate():
    """首页首屏聚合数据：统计 + 教程分类 + 各板块热门列表"""
    global _LAST_GOOD
    payload = await _cached_payload()
    if payload is not None:
        return success_response(payload)

    async with _single_flight() as exclusive:
        # 双重检查：等待锁期间缓存可能已被并发请求回填
        payload = await _cached_payload()
        if payload is not None:
            return success_response(payload)

        # 放弃排他（等锁超时）说明已有慢请求在重建聚合，此时再扇出 8 条并发查询是火上浇油：
        # 优先复用进程内上一次成功结果
        if not exclusive and _LAST_GOOD is not None:
            built_at, stale = _LAST_GOOD
            if time.monotonic() - built_at <= _STALE_MAX_AGE:
                logger.warning("首页聚合等锁超时，返回上一次成功结果（陈旧但可用）")
                return success_response(stale)

        # AsyncSession 不支持并发复用，每个子查询独立开 Session 后并发执行
        stats, tutorials, nodes, tools, resources, plugins, categories, announcements = await asyncio.gather(
            _run_with_session(get_public_stats),
            _run_with_session(lambda db: get_tutorials(db, 1, 8, sort="hottest")),
            _run_with_session(lambda db: list_nodes(db, 1, 5)),
            _run_with_session(lambda db: get_tools(db, 1, 5)),
            _run_with_session(lambda db: get_resources(db, 1, 9, sort_by="download_count")),
            _run_with_session(lambda db: list_plugins(db, 1, 5)),
            _run_with_session(lambda db: list_categories_with_tutorial_count(db)),
            _run_with_session(lambda db: list_latest_announcements(db, 5)),
        )

        data = {
            "stats": stats or _EMPTY_STATS,
            "tutorials": tutorials or _EMPTY_PAGE,
            "nodes": nodes or _EMPTY_PAGE,
            "tools": tools or _EMPTY_PAGE,
            "resources": resources or _EMPTY_PAGE,
            "plugins": plugins or _EMPTY_PAGE,
            "categories": categories or [],
            "announcements": announcements or [],
        }

        await _cache_set(
            home_aggregate_key(), _ttl_with_jitter(CACHE_TTL), json.dumps(data, ensure_ascii=False)
        )
        # 记一份给「等锁超时」的请求兜底（P1-59）
        _LAST_GOOD = (time.monotonic(), data)
        return success_response(data)
