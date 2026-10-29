"""节点百科路由"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ContentListItem, PageResult, NodeDetail
from app.services.node_service import list_nodes, get_node_stats, get_node_detail
from app.schemas.admin import StatsResult

router = APIRouter()


@router.get("", response_model=ApiResponse[PageResult[ContentListItem]])
async def list_nodes_route(
    # 上限 200：OFFSET 深分页会让 MySQL 扫完前面所有行（page=100000 就是 500 万行）
    # 公开列表不需要翻到那么深，超出的长尾交给搜索/sitemap（第五轮审查 PERF-08）
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(12, ge=1, le=50),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None, max_length=50),
    sort: str = Query("latest", pattern="^(latest|hottest|recommended)$"),
    db: AsyncSession = Depends(get_db),
):
    """节点列表 (公开)

    sort 取值:
    - latest: 最新更新（按 updated_at 倒序）
    - hottest: 最热浏览（按 view_count 倒序）
    - recommended: 推荐（综合 view_count + id）
    """
    result = await list_nodes(db, page, page_size, keyword, category, sort)
    return success_response(result)


@router.get("/stats", response_model=ApiResponse[StatsResult])
async def nodes_stats(db: AsyncSession = Depends(get_db)):
    """节点分类统计 (公开)"""
    result = await get_node_stats(db)
    return success_response(result)


@router.get("/{node_id}", response_model=ApiResponse[NodeDetail])
async def node_detail(node_id: int, db: AsyncSession = Depends(get_db)):
    """节点详情 (公开) - 自增浏览量"""
    result = await get_node_detail(db, node_id)
    return success_response(result)
