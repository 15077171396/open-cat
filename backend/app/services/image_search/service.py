"""图片搜索编排：分源检索、缓存、熔断、额度与本站素材。"""

import asyncio
import hashlib
import json
import time
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timezone

from loguru import logger
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard, degrade_open
from app.core.exceptions import AppException
from app.models.image_asset import ImageAsset
from app.models.user import User
from app.redis import redis_client
from app.services import cos_service
from app.services.admin_audit_service import record_admin_action
from app.services.image_search.base import ImageHit, SearchQuery
from app.services.image_search.config import (
    SOURCE_META, ImageSearchConfig, load_config, options_payload,
)
from app.services.image_search.local import get_asset, search_local
from app.services.image_search.providers import build_provider
from app.services.image_search.thumb import filter_dead_urls

CACHE_TTL = 600
ITEM_TTL = 1800
PROVIDER_TIMEOUT = 8.0
BREAKER_THRESHOLD = 3
BREAKER_TTL = 300
IMPORT_HOURLY_LIMIT = 60

# 每个图源的限流值随图源一起定义在 SOURCE_META["rate_limit"]，这里不再维护第二份。
_REDIS_ERRORS = (RedisError, OSError, TimeoutError)


def _bucket(window: int) -> int:
    return int(time.time() // window)


def _cache_key(source: str, query: SearchQuery) -> str:
    digest = hashlib.sha1(
        f"{query.q}|{query.image_type}|{query.orientation}|{query.page}|{query.per_page}".encode()
    ).hexdigest()
    return f"image_search:cache:{source}:{digest}"


def _item_key(source: str, source_id: str) -> str:
    return f"image_search:item:{source}:{source_id}"


def _breaker_fail_key(source: str) -> str:
    return f"image_search:breaker:fail:{source}"


def _breaker_open_key(source: str) -> str:
    return f"image_search:breaker:open:{source}"


async def _cache_get(key: str) -> dict | None:
    try:
        raw = await redis_client.get(key)
    except _REDIS_ERRORS as exc:
        degrade_open("image_search.cache_get", exc, detail="搜索缓存读取失败，直接回源")
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def _cache_set(key: str, value: dict, ttl: int) -> None:
    try:
        await redis_client.setex(key, ttl, json.dumps(value, ensure_ascii=False))
    except _REDIS_ERRORS as exc:
        degrade_open("image_search.cache_set", exc, detail="搜索缓存写入失败，不影响本次结果")


async def _remember_items(items: list[ImageHit]) -> None:
    for hit in items:
        await _cache_set(_item_key(hit.source, hit.source_id), asdict(hit), ITEM_TTL)


async def _drop_dead_items(items: list[ImageHit]) -> list[ImageHit]:
    """剔掉已确认失效的图（图源侧 404/防盗链）。

    不在这一层拦，前端就只能靠「请求 → 502 → onError 隐藏」来兜底，
    网络面板里每次搜索都是一片红。这里提前剔掉，前端根本不会去请求。
    只按缩略图判断：缩略图能出，卡片就能用，预览图失效不影响展示。
    """
    if not items:
        return items
    dead = await filter_dead_urls(
        [url for hit in items for url in (hit.thumb_url, hit.download_url) if url]
    )
    if not dead:
        return items
    return [hit for hit in items if hit.thumb_url not in dead]


async def _breaker_is_open(source: str) -> bool:
    try:
        return bool(await redis_client.get(_breaker_open_key(source)))
    except _REDIS_ERRORS:
        return False


async def _breaker_failure(source: str) -> None:
    try:
        count = await redis_client.incr(_breaker_fail_key(source))
        if count == 1:
            await redis_client.expire(_breaker_fail_key(source), BREAKER_TTL)
        if count >= BREAKER_THRESHOLD:
            await redis_client.setex(_breaker_open_key(source), BREAKER_TTL, "1")
            logger.warning(f"图片图源熔断 {source}：连续失败 {count} 次，暂停 {BREAKER_TTL}s")
    except _REDIS_ERRORS:
        pass


async def _breaker_success(source: str) -> None:
    with suppress(*_REDIS_ERRORS):
        await redis_client.delete(_breaker_fail_key(source), _breaker_open_key(source))


async def _incr_window(key: str, window: int) -> int | None:
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, window + 5)
        return count
    except _REDIS_ERRORS as exc:
        degrade_open("image_search.quota", exc, detail="额度计数不可用，放行本次请求")
        return None


async def _check_admin_hourly(admin_id: int, cfg: ImageSearchConfig) -> None:
    if cfg.hourly_limit <= 0:
        return
    key = f"image_search:admin:{admin_id}:search:{_bucket(3600)}"
    count = await _incr_window(key, 3600)
    if count is not None and count > cfg.hourly_limit:
        raise AppException(code=429, message="图片搜索太频繁，请稍后再试")


async def _check_provider_quota(source: str) -> None:
    limit, window = SOURCE_META[source]["rate_limit"]
    key = f"image_search:provider:{source}:{_bucket(window)}"
    count = await _incr_window(key, window)
    if count is not None and count > limit:
        name = SOURCE_META[source]["name"]
        raise AppException(code=429, message=f"{name} 请求额度已用完，请稍后再试或切换图源")


async def check_import_quota(cfg: ImageSearchConfig, admin_id: int) -> None:
    if cfg.daily_import_limit <= 0 and IMPORT_HOURLY_LIMIT <= 0:
        return
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    try:
        daily = await redis_client.get(f"image_search:import:daily:{day}")
        hourly = await redis_client.get(
            f"image_search:import:admin:{admin_id}:{_bucket(3600)}"
        )
    except _REDIS_ERRORS as exc:
        degrade_open("image_search.import_quota", exc, detail="导入额度不可用，放行本次请求")
        return
    if cfg.daily_import_limit > 0 and int(daily or 0) >= cfg.daily_import_limit:
        raise AppException(code=429, message="今日图片导入额度已用完，请明天再试")
    if IMPORT_HOURLY_LIMIT > 0 and int(hourly or 0) >= IMPORT_HOURLY_LIMIT:
        raise AppException(code=429, message="图片导入太频繁，请稍后再试")


async def record_import_success(admin_id: int) -> None:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    with suppress(*_REDIS_ERRORS):
        daily_key = f"image_search:import:daily:{day}"
        await redis_client.incr(daily_key)
        await redis_client.expire(daily_key, 86400 + 3600)
        hourly_key = f"image_search:import:admin:{admin_id}:{_bucket(3600)}"
        await redis_client.incr(hourly_key)
        await redis_client.expire(hourly_key, 3600 + 5)


async def audit_import(db: AsyncSession, admin: User, asset: ImageAsset) -> None:
    """导入成功后写审计；审计失败只降级记录，不能把已完成的导入变成 500。"""
    with degrade_guard("image_search.audit", detail="导入审计写入失败，不影响图片已入库"):
        record_admin_action(
            db, admin, "image_search_import",
            target_type="image_asset", target_id=asset.id,
            summary=f"导入图库图片（{asset.source}）",
            detail={
                "source": asset.source,
                "source_id": asset.source_id,
                "license": asset.license,
            },
        )
        await db.commit()


async def build_options(db: AsyncSession) -> dict:
    cfg = await load_config(db)
    return {
        "enabled": cfg.enabled,
        "page_size": cfg.page_size,
        "cos_configured": cos_service.is_configured(),
        "local_available": True,
        "sources": options_payload(cfg),
    }


async def search(
    db: AsyncSession,
    *,
    admin_id: int,
    source: str,
    q: str,
    page: int,
    per_page: int,
    image_type: str,
    orientation: str,
) -> dict:
    cfg = await load_config(db)
    if not cfg.enabled:
        raise AppException(code=503, message="图片搜索未开启，请联系管理员")
    query = (q or "").strip()
    if len(query) > 80:
        raise AppException(code=422, message="搜索关键词不能超过 80 个字符")
    if not 1 <= page <= 50:
        raise AppException(code=422, message="页码超出范围")
    if not 1 <= per_page <= 40:
        raise AppException(code=422, message="每页数量超出范围")
    if source == "local":
        return await search_local(db, query, page, per_page)
    if source not in SOURCE_META:
        raise AppException(code=400, message="不支持的图片图源")
    if not cfg.has_source(source):
        name = SOURCE_META[source]["name"]
        raise AppException(code=503, message=f"{name} 未配置 API Key，请联系管理员")
    if not query:
        raise AppException(code=422, message="请输入搜索关键词")
    try:
        search_query = SearchQuery(
            q=query, page=page, per_page=per_page,
            image_type=image_type, orientation=orientation,
        )
    except ValueError as exc:
        raise AppException(code=422, message="搜索筛选条件不合法") from exc

    await _check_admin_hourly(admin_id, cfg)
    cache_key = _cache_key(source, search_query)
    cached = await _cache_get(cache_key)
    if cached is not None:
        cached["cached"] = True
        return cached
    if await _breaker_is_open(source):
        raise AppException(code=503, message=f"{SOURCE_META[source]['name']} 暂时不可用，请稍后重试")

    await _check_provider_quota(source)
    provider = build_provider(source, cfg.keys[source])
    try:
        try:
            page_data = await asyncio.wait_for(provider.search(search_query), timeout=PROVIDER_TIMEOUT)
        except TimeoutError as exc:
            raise AppException(code=504, message=f"{SOURCE_META[source]['name']} 请求超时") from exc
        await _breaker_success(source)
        items = await _drop_dead_items(page_data.items)
        payload = {
            "source": source,
            "items": [hit.public_dict() for hit in items],
            "has_more": page_data.has_more,
            "total": page_data.total,
            "cached": False,
        }
        await _cache_set(cache_key, payload, CACHE_TTL)
        await _remember_items(page_data.items)
        logger.info(
            f"图片搜索 source={source} page={page} results={len(items)} q={query[:40]}"
        )
        return payload
    except AppException:
        await _breaker_failure(source)
        raise
    finally:
        with suppress(Exception):
            await provider.close()


async def resolve_hit(db: AsyncSession, *, source: str, source_id: str) -> ImageHit:
    cfg = await load_config(db)
    if not cfg.enabled:
        raise AppException(code=503, message="图片搜索未开启，请联系管理员")
    if source not in SOURCE_META:
        raise AppException(code=400, message="不支持的图片图源")
    if not cfg.has_source(source):
        raise AppException(code=503, message=f"{SOURCE_META[source]['name']} 未配置 API Key")
    cached = await _cache_get(_item_key(source, source_id))
    if cached:
        try:
            return ImageHit(**cached)
        except TypeError:
            pass
    provider = build_provider(source, cfg.keys[source])
    try:
        try:
            hit = await asyncio.wait_for(provider.get(source_id), timeout=PROVIDER_TIMEOUT)
        except TimeoutError as exc:
            raise AppException(code=504, message="图片详情请求超时，请重新搜索") from exc
    finally:
        with suppress(Exception):
            await provider.close()
    if hit is None:
        raise AppException(code=404, message="图片已失效，请重新搜索")
    await _cache_set(_item_key(source, source_id), asdict(hit), ITEM_TTL)
    return hit


async def resolve_local_asset(db: AsyncSession, source_id: str):
    return await get_asset(db, source_id)
