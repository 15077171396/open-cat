"""资源路由"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_optional
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ContentListItem, ItemsResult, PageResult, ResourceDetail
from app.services.resource_service import (
    get_resources,
    get_resource_detail,
    get_download_links,
    get_resource_stats,
    get_hot_resources,
    get_latest_resources,
)
from app.schemas.admin import StatsResult
from typing import Any

router = APIRouter()


@router.get("", response_model=ApiResponse[PageResult[ContentListItem]])
async def list_resources(
    # 上限 200：OFFSET 深分页会让 MySQL 扫完前面所有行（page=100000 就是 500 万行）
    # 公开列表不需要翻到那么深，超出的长尾交给搜索/sitemap（第五轮审查 PERF-08）
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(8, ge=1, le=50),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    resource_type: str = Query(None, max_length=50),
    sort_by: str = Query(None, pattern="^(updated_at|download_count)$"),
    db: AsyncSession = Depends(get_db),
):
    """资源列表 (公开)

    sort_by 取值:
    - updated_at: 最新发布（默认）
    - download_count: 热门下载
    """
    result = await get_resources(db, page, page_size, keyword, resource_type, sort_by)
    return success_response(result)


@router.get("/stats", response_model=ApiResponse[StatsResult])
async def list_resource_stats(db: AsyncSession = Depends(get_db)):
    """资源分类统计 (公开)"""
    result = await get_resource_stats(db)
    return success_response(result)


@router.get("/hot", response_model=ApiResponse[ItemsResult[ContentListItem]])
async def list_hot_resources(
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """热门下载资源 (公开)"""
    result = await get_hot_resources(db, limit)
    return success_response(result)


@router.get("/latest", response_model=ApiResponse[ItemsResult[ContentListItem]])
async def list_latest_resources(
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """最新资源 (公开)"""
    result = await get_latest_resources(db, limit)
    return success_response(result)


@router.get("/{resource_id}", response_model=ApiResponse[ResourceDetail])
async def resource_detail(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """资源详情 (公开, 网盘链接需登录)"""
    result = await get_resource_detail(db, resource_id, current_user)
    return success_response(result)


@router.post("/{resource_id}/get-links", response_model=ApiResponse[PageResult[Any]])
async def get_resource_links(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取网盘下载链接 (需登录)"""
    result = await get_download_links(db, current_user, resource_id)
    return success_response(result, "获取成功")
