"""扩展推荐路由"""

from fastapi import APIRouter, Depends, Query
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.legacy_metrics import record_legacy_usage
from app.database import get_db
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ItemsResult, ContentListItem, PageResult, PluginDetail
from app.services.plugin_service import (
    LEGACY_SORT_ALIASES,
    list_plugins,
    get_featured_plugins,
    get_plugin_detail,
)

router = APIRouter()


@router.get("", response_model=ApiResponse[PageResult[ContentListItem]])
async def list_plugins_route(
    # 上限 200：OFFSET 深分页会让 MySQL 扫完前面所有行（page=100000 就是 500 万行）
    # 公开列表不需要翻到那么深，超出的长尾交给搜索/sitemap（第五轮审查 PERF-08）
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(8, ge=1, le=50),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None, max_length=50),
    # hottest/rating 原本的排序列已删除，但白名单不能直接收窄：已发布的小程序包仍会发
    # 这两个值（小程序更新受微信审核控制，无法与后端同批上线），收窄会让旧包整页 422。
    # 保留取值并映射到 view_count 排序（别名表见 plugin_service）；摘除判据: plugin_legacy_sort 连续 7 天为 0
    sort: str = Query("comprehensive", pattern="^(comprehensive|latest|hottest|rating)$"),
    db: AsyncSession = Depends(get_db),
):
    """扩展列表 (公开)"""
    if sort in LEGACY_SORT_ALIASES:
        # 日志给人看，计数给看板用：摘除判据是「连续 7 天为 0」，只有日志等于永远摘不掉
        logger.bind(event="plugin_legacy_sort", sort=sort).info(
            f"扩展列表收到已下线的历史排序值 sort={sort}，按 view_count 处理（旧小程序包兼容分支）"
        )
        await record_legacy_usage("plugin_legacy_sort")
    result = await list_plugins(db, page, page_size, keyword, category, sort)
    return success_response(result)


@router.get("/featured", response_model=ApiResponse[ItemsResult[ContentListItem]])
async def featured_plugins(
    limit: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """推荐扩展轮播 (公开) - 取综合排序前 N 条"""
    result = await get_featured_plugins(db, limit)
    return success_response(result)


@router.get("/{plugin_id}", response_model=ApiResponse[PluginDetail])
async def plugin_detail(plugin_id: int, db: AsyncSession = Depends(get_db)):
    """扩展详情 (公开) - 自增浏览量"""
    result = await get_plugin_detail(db, plugin_id)
    return success_response(result)
