"""管理后台 - 在线工具管理"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Literal, Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ContentListItem, PageResult
from app.api._admin_shared import router

from app.services.admin_tool_service import (
    list_tools,
    get_tool,
    create_tool,
    update_tool,
    toggle_publish as toggle_tool_publish,
    delete_tool,
)
from app.schemas.content import ToolDetail
from app.schemas.admin import ActionAck, CreatedResult


# 在线工具管理

class ToolCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    category: str = Field("其他", min_length=1, max_length=30)
    tool_type: Literal["ai", "frontend"] = "ai"
    ai_config: Optional[dict] = None
    form_schema: Optional[dict] = None
    is_published: bool = True
    # 使用权限：free=免费 / auth=需认证(默认) / points=需积分
    access_mode: Literal["free", "auth", "points"] = "auth"
    # 积分模式下单次运行消耗的积分（仅 AI 工具生效）
    points_cost: int = Field(1, ge=0, le=100000)


class ToolUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    category: Optional[str] = Field(None, min_length=1, max_length=30)
    tool_type: Optional[Literal["ai", "frontend"]] = None
    ai_config: Optional[dict] = None
    form_schema: Optional[dict] = None
    is_published: Optional[bool] = None
    access_mode: Optional[Literal["free", "auth", "points"]] = None
    points_cost: Optional[int] = Field(None, ge=0, le=100000)


@router.get("/tools", response_model=ApiResponse[PageResult[ContentListItem]])
async def admin_list_tools(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None),
    tool_type: str = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """工具列表"""
    result = await list_tools(db, page, page_size, keyword, category, tool_type)
    return success_response(result)


@router.get("/tools/{tool_id}", response_model=ApiResponse[ToolDetail])
async def admin_get_tool(
    tool_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """工具详情"""
    result = await get_tool(db, tool_id)
    return success_response(result)


@router.post("/tools", response_model=ApiResponse[CreatedResult])
async def admin_create_tool(
    req: ToolCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建工具"""
    result = await create_tool(db, req.model_dump(), admin)
    return success_response(result, "工具创建成功")


@router.put("/tools/{tool_id}", response_model=ApiResponse[None])
async def admin_update_tool(
    tool_id: int,
    req: ToolUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新工具"""
    await update_tool(db, tool_id, req.model_dump(exclude_unset=False), admin)
    return success_response(message="工具已更新")


@router.patch("/tools/{tool_id}/publish", response_model=ApiResponse[ActionAck])
async def admin_toggle_tool_publish(
    tool_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """切换工具上架状态"""
    result = await toggle_tool_publish(db, tool_id, admin)
    return success_response(result, "状态已更新")


@router.delete("/tools/{tool_id}", response_model=ApiResponse[None])
async def admin_delete_tool(
    tool_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除工具"""
    await delete_tool(db, tool_id, admin)
    return success_response(message="工具已删除")
