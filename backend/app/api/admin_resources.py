"""管理后台 - 资源管理"""

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

from app.services.admin_resource_service import (
    list_resources,
    get_resource,
    create_resource,
    update_resource,
    delete_resource,
    batch_delete_resources,
    restore_resource,
    batch_restore_resources,
)
from app.api._admin_shared import NetdiskLinkItem
from app.schemas.content import ResourceDetail
from app.schemas.admin import ActionAck, AffectedResult, CreatedResult, DeletedResult


# 资源管理

class ResourceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)
    resource_type: str = Field(..., min_length=1, max_length=30)
    file_size: Optional[str] = Field(None, min_length=1, max_length=20)
    content: Optional[str] = None
    content_list: Optional[list] = None
    usage_notes: Optional[list] = None
    # 精选推荐：True 时前台资源页「精选资源推荐」优先展示
    is_featured: Optional[bool] = False
    # 置顶：True 时前台资源列表最优先展示
    is_pinned: Optional[bool] = False
    # 封面图 URL（前台卡片封面与详情页 Hero 背景）
    cover_image: Optional[str] = Field(None, max_length=500)
    # 发布作者：**没有手工输入 UI**（2026-10-12 需求：不该让运营手填）。
    # 仅「第三方导入」会自动带上原文/仓库作者；缺省时由发布账号兜底（见 content_author）。
    author_name: Optional[str] = Field(None, max_length=100)
    author_avatar_url: Optional[str] = Field(None, max_length=500)
    # 资源标签（JSON 数组）
    tags: Optional[list] = None
    # SEO 信息：仅写入页面 meta 标签供搜索引擎抓取，前台页面不展示
    seo_title: Optional[str] = Field(None, max_length=200)
    seo_description: Optional[str] = Field(None, max_length=500)
    seo_keywords: Optional[str] = Field(None, max_length=500)
    # 发布状态：False=草稿，前台列表/详情/首页/sitemap 均不可见。默认 True（保存即上线）
    is_published: Optional[bool] = True
    netdisk_links: Optional[list[NetdiskLinkItem]] = None


class ResourceUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, min_length=1)
    content: Optional[str] = None
    resource_type: Optional[str] = Field(None, min_length=1, max_length=30)
    file_size: Optional[str] = Field(None, min_length=1, max_length=20)
    content_list: Optional[list] = None
    usage_notes: Optional[list] = None
    is_featured: Optional[bool] = None
    is_pinned: Optional[bool] = None
    cover_image: Optional[str] = Field(None, max_length=500)
    tags: Optional[list] = None
    # 发布作者：同 Create —— 无手工 UI；缺省时由发布账号在「作者为空」时兜底
    author_name: Optional[str] = Field(None, max_length=100)
    author_avatar_url: Optional[str] = Field(None, max_length=500)
    # SEO 信息：允许清空（传空串 = 清除，回落默认标题/简介）
    seo_title: Optional[str] = Field(None, max_length=200)
    seo_description: Optional[str] = Field(None, max_length=500)
    seo_keywords: Optional[str] = Field(None, max_length=500)
    # 发布状态：显式传值才改；None=不改动（草稿/下架都走这里，漏声明会被 pydantic 静默丢弃）
    is_published: Optional[bool] = None
    netdisk_links: Optional[list[NetdiskLinkItem]] = None


@router.get("/resources", response_model=ApiResponse[PageResult[ContentListItem]])
async def admin_list_resources(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH, description="名称/简介关键词"),
    resource_type: str = Query(None, max_length=30, description="资源类型筛选"),
    is_featured: bool = Query(None, description="精选推荐筛选"),
    deleted: Literal["active", "deleted", "all"] = Query(
        "active", description="回收站视图：active=正常内容（默认）/ deleted=回收站 / all=全部"
    ),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """资源列表（支持关键词搜索与类型/精选筛选）

    `deleted` 是**视图**而非筛选项：默认只看正常内容。
    """
    result = await list_resources(db, page, page_size, keyword, resource_type, is_featured, deleted)
    return success_response(result)


# 资源批量操作（批量路由必须定义在 /resources/{resource_id} 之前，
# 否则 "batch-delete" 会被当作路径参数解析为 int 而返回 422）

class ResourceBatchDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.post("/resources/batch-delete", response_model=ApiResponse[DeletedResult])
async def admin_batch_delete_resources(
    req: ResourceBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量删除资源（**移入回收站**，可恢复；网盘链接保留）"""
    result = await batch_delete_resources(db, req.ids, admin)
    return success_response(result, f"已删除 {result['deleted']} 个资源（可在回收站恢复）")


@router.post("/resources/batch-restore", response_model=ApiResponse[AffectedResult])
async def admin_batch_restore_resources(
    req: ResourceBatchDeleteRequest,
    republish: bool = Query(False, description="恢复的同时重新发布（默认恢复为草稿）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量从回收站恢复资源（默认恢复为草稿）"""
    result = await batch_restore_resources(db, req.ids, admin, republish=republish)
    return success_response(result, f"已恢复 {result['restored']} 个资源")


@router.get("/resources/{resource_id}", response_model=ApiResponse[ResourceDetail])
async def admin_get_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """资源详情"""
    result = await get_resource(db, resource_id)
    return success_response(result)


@router.post("/resources", response_model=ApiResponse[CreatedResult])
async def admin_create_resource(
    req: ResourceCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建资源"""
    result = await create_resource(db, req.model_dump(), admin)
    return success_response(result, "资源创建成功")


@router.put("/resources/{resource_id}", response_model=ApiResponse[None])
async def admin_update_resource(
    resource_id: int,
    req: ResourceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新资源"""
    await update_resource(db, resource_id, req.model_dump(exclude_unset=False), admin)
    return success_response(message="资源已更新")


@router.delete("/resources/{resource_id}", response_model=ApiResponse[None])
async def admin_delete_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除资源（软删除：移入回收站，可在回收站恢复）"""
    await delete_resource(db, resource_id, admin)
    return success_response(message="资源已移入回收站")


@router.post("/resources/{resource_id}/restore", response_model=ApiResponse[ActionAck])
async def admin_restore_resource(
    resource_id: int,
    republish: bool = Query(False, description="恢复的同时重新发布（默认恢复为草稿）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """从回收站恢复资源（默认恢复为草稿，传 republish=true 时同时上线）"""
    result = await restore_resource(db, resource_id, admin, republish=republish)
    return success_response(result, "资源已恢复" + ("并发布" if republish else "（草稿）"))
