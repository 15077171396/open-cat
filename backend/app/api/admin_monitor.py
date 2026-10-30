"""监控管理：全站登录会话 / 宿主机资源 / Redis 缓存（只读）。"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.monitor import CacheStatsData, OnlineSessionsData, ServerStatsData
from app.services import monitor_service


@router.get("/monitor/online", response_model=ApiResponse[OnlineSessionsData])
async def online_sessions(
    keyword: str = Query("", max_length=KEYWORD_MAX_LENGTH),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(
        await monitor_service.list_online_sessions(
            db, keyword=keyword, page=page, page_size=page_size
        )
    )


@router.get("/monitor/server", response_model=ApiResponse[ServerStatsData])
async def server_stats(admin: User = Depends(require_admin)):
    return success_response(await monitor_service.get_server_stats())


@router.get("/monitor/cache", response_model=ApiResponse[CacheStatsData])
async def cache_stats(admin: User = Depends(require_admin)):
    return success_response(await monitor_service.get_cache_stats())
