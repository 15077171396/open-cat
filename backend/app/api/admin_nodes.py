"""管理后台 - 知识百科（节点）管理"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ContentListItem, PageResult
from app.api._admin_shared import router

from app.services.admin_node_service import (
    list_nodes,
    get_node,
    create_node,
    update_node,
    delete_node,
    batch_delete_nodes,
)
from app.schemas.content import NodeDetail
from app.schemas.admin import CreatedResult, DeletedResult


# 知识百科管理

class NodeCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=50)
    description: str = Field(..., min_length=1)
    content: Optional[str] = None
    inputs: Optional[list] = None
    outputs: Optional[list] = None
    params: Optional[list] = None
    examples: Optional[list] = None
    # 封面图（与资源同一约定），可空，空串=清除
    cover_image: Optional[str] = Field(None, max_length=500)
    # 发布作者：**没有手工输入 UI**（2026-10-12 需求：不该让运营手填）。
    # 仅「第三方导入」会自动带上原文/仓库作者；缺省时由发布账号兜底（见 content_author）。
    author_name: Optional[str] = Field(None, max_length=100)
    author_avatar_url: Optional[str] = Field(None, max_length=500)
    # SEO 信息：仅写入详情页 meta 标签供搜索引擎抓取，前台不展示
    seo_title: Optional[str] = Field(None, max_length=200)
    seo_description: Optional[str] = Field(None, max_length=500)
    seo_keywords: Optional[str] = Field(None, max_length=500)
    # 发布状态：False=草稿，前台列表/详情/首页/sitemap 均不可见。默认 True（保存即上线）
    is_published: Optional[bool] = True


class NodeUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    category: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, min_length=1)
    content: Optional[str] = None
    inputs: Optional[list] = None
    outputs: Optional[list] = None
    params: Optional[list] = None
    examples: Optional[list] = None
    # 封面图（与资源/扩展同一约定），可空，空串=清除
    cover_image: Optional[str] = Field(None, max_length=500)
    # 发布作者：同 Create —— 无手工 UI；缺省时由发布账号在「作者为空」时兜底
    author_name: Optional[str] = Field(None, max_length=100)
    author_avatar_url: Optional[str] = Field(None, max_length=500)
    # SEO 信息：允许清空（传空串 = 清除，回落默认标题）
    seo_title: Optional[str] = Field(None, max_length=200)
    seo_description: Optional[str] = Field(None, max_length=500)
    seo_keywords: Optional[str] = Field(None, max_length=500)
    # 发布状态：显式传值才改；None=不改动（草稿/下架都走这里，漏声明会被 pydantic 静默丢弃）
    is_published: Optional[bool] = None


@router.get("/nodes", response_model=ApiResponse[PageResult[ContentListItem]])
async def admin_list_nodes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """节点列表"""
    result = await list_nodes(db, page, page_size, keyword, category)
    return success_response(result)


# 节点批量操作（批量路由必须定义在 /nodes/{node_id} 之前，
# 否则 "batch-delete" 会被当作路径参数解析为 int 而返回 422）

class NodeBatchDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.post("/nodes/batch-delete", response_model=ApiResponse[DeletedResult])
async def admin_batch_delete_nodes(
    req: NodeBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量删除节点（不可恢复）"""
    result = await batch_delete_nodes(db, req.ids, admin)
    return success_response(result, f"已删除 {result['deleted']} 个节点")


@router.get("/nodes/{node_id}", response_model=ApiResponse[NodeDetail])
async def admin_get_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """节点详情"""
    result = await get_node(db, node_id)
    return success_response(result)


@router.post("/nodes", response_model=ApiResponse[CreatedResult])
async def admin_create_node(
    req: NodeCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建节点"""
    result = await create_node(db, req.model_dump(), admin)
    return success_response(result, "节点创建成功")


@router.put("/nodes/{node_id}", response_model=ApiResponse[None])
async def admin_update_node(
    node_id: int,
    req: NodeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新节点"""
    await update_node(db, node_id, req.model_dump(exclude_unset=False), admin)
    return success_response(message="节点已更新")


@router.delete("/nodes/{node_id}", response_model=ApiResponse[None])
async def admin_delete_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除节点"""
    await delete_node(db, node_id, admin)
    return success_response(message="节点已删除")
