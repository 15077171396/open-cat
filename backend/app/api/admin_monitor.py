"""监控管理：全站登录会话 / 宿主机资源 / Redis 缓存 / 系统错误日志（只读）。"""

import json

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.degrade import degrade_guard
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.logging import ERROR_LOG_KEY
from app.models.user import User
from app.redis import redis_client
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.monitor import (
    CacheStatsData,
    ErrorLogItem,
    ErrorLogsData,
    OnlineSessionsData,
    ServerStatsData,
)
from app.services import api_stats_service, monitor_service
from app.schemas.admin import ActionAck


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


@router.get("/monitor/api-stats", response_model=ApiResponse[ActionAck])
async def api_stats_overview(
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
):
    """接口调用统计：调用量 / 错误率 Top + 慢接口明细（当前小时 + 上一小时聚合）。

    统计开关关闭或 Redis 不可用 → available=False（页面显示「尚未采集」），不 500。
    """
    return success_response(await api_stats_service.get_overview(limit))


@router.get("/monitor/errors", response_model=ApiResponse[ErrorLogsData])
async def list_error_logs(
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_admin),
):
    """最近系统错误事件（Redis 环形缓冲，最多保留 500 条）。

    Redis 不可用/缓冲为空 → available=False 的空列表（页面显示「暂无错误事件」
    或「不可用」），**不 500** —— 观测接口绝不反噬主流程。
    """
    data = ErrorLogsData()
    with degrade_guard("monitor.errors", detail="错误日志读取失败（Redis 不可用？）"):
        raw_items = await redis_client.lrange(ERROR_LOG_KEY, 0, limit - 1)
        for raw in raw_items:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                data.items.append(ErrorLogItem(**payload))
    if data.items:
        data.available = True
    else:
        # 区分「真的没有错误」与「读不到」：读不到时给 error 提示
        data.available = False
        data.error = "暂无错误事件（或 Redis 不可用无法读取）"
    return success_response(data)
