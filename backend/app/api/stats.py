"""公开统计路由 (首页数据卡片)"""

import json
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse
from app.redis import log_redis_degraded, redis_client
from app.services.stats_service import get_public_stats
from app.services.content_cache import PUBLIC_STATS_TTL, public_stats_key
from app.schemas.admin import StatsResult

router = APIRouter()


@router.get("", response_model=ApiResponse[StatsResult])
async def public_stats(db: AsyncSession = Depends(get_db)):
    """首页公开统计数据 (教程/资源/扩展/用户总数)"""
    # 键与 TTL 来自 content_cache：写死字面量会让管理端写操作无从失效它（首页卡片一直显示旧数字）
    cache_key = public_stats_key()
    # Redis 故障降级直查 DB（缓存用途不应放大为 500）
    try:
        cached = await redis_client.get(cache_key)
    except Exception:
        log_redis_degraded("stats.public_stats_cache", "Redis 不可用，公开统计缓存降级直查 DB")
        cached = None
    if cached:
        return success_response(json.loads(cached))

    stats = await get_public_stats(db)

    try:
        await redis_client.set(cache_key, json.dumps(stats, ensure_ascii=False), ex=PUBLIC_STATS_TTL)
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("stats.public_stats", exc)
    return success_response(stats)
