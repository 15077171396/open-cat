"""工具路由"""

from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ContentListItem, PageResult, ToolDetail
from app.services.tool_service import get_tools, get_tool_detail, run_tool
from app.schemas.admin import ActionAck

router = APIRouter()


@router.get("", response_model=ApiResponse[PageResult[ContentListItem]])
async def list_tools(
    # 上限 200：OFFSET 深分页会让 MySQL 扫完前面所有行（page=100000 就是 500 万行）
    # 公开列表不需要翻到那么深，超出的长尾交给搜索/sitemap（第五轮审查 PERF-08）
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(8, ge=1, le=50),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None, max_length=50),
    db: AsyncSession = Depends(get_db),
):
    """工具列表 (公开)"""
    result = await get_tools(db, page, page_size, keyword, category)
    return success_response(result)


@router.get("/{tool_id}", response_model=ApiResponse[ToolDetail])
async def tool_detail(tool_id: int, db: AsyncSession = Depends(get_db)):
    """工具详情 (公开)"""
    result = await get_tool_detail(db, tool_id)
    return success_response(result)


@router.post("/{tool_id}/run", response_model=ApiResponse[ActionAck])
async def run(
    tool_id: int,
    input_data: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """运行工具 (需登录, 计时)"""
    result = await run_tool(db, current_user, tool_id, input_data)
    return success_response(result)
