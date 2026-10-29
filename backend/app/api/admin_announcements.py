"""管理后台 - 公告管理"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.api._admin_shared import router

from app.services.admin_announcement_service import (
    admin_list_announcements,
    admin_create_announcement,
    admin_update_announcement,
    admin_delete_announcement,
    admin_batch_delete_announcements,
    admin_batch_publish_announcements,
)
from app.schemas.content import PageResult
from app.schemas.admin import AffectedResult, AnnouncementItem, CreatedResult, DeletedResult


# 公告管理

class AnnouncementCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    tag: str = Field("公告", min_length=1, max_length=20)
    color: str = Field("#3b82f6", min_length=1, max_length=20)
    is_published: bool = True
    # 定时发布：不传 / null = 立即；未来时间 = 到点才可见（is_published=1 也要等到点）
    publish_at: Optional[datetime] = None


class AnnouncementUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, min_length=1)
    tag: Optional[str] = Field(None, min_length=1, max_length=20)
    color: Optional[str] = Field(None, min_length=1, max_length=20)
    is_published: Optional[bool] = None
    # 定时发布：传 null 表示取消定时（立即生效）
    publish_at: Optional[datetime] = None


@router.get("/announcements", response_model=ApiResponse[PageResult[AnnouncementItem]])
async def admin_list_announcements_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    is_published: Optional[bool] = Query(None),
    tag: str = Query(None, max_length=20),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """公告列表（含未发布草稿）；tag 按标签精确筛选（取值见前端的公告标签预设）"""
    result = await admin_list_announcements(db, page, page_size, keyword, is_published, tag)
    return success_response(result)


@router.post("/announcements", response_model=ApiResponse[CreatedResult])
async def admin_create_announcement_api(
    req: AnnouncementCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建公告"""
    result = await admin_create_announcement(db, admin, req.model_dump())
    return success_response(result, "公告创建成功")


# 公告批量操作
# 注意：批量路由必须定义在 /announcements/{announcement_id} 之前，
# 否则 "batch-delete" 会被当作路径参数解析为 int 而返回 422。

class AnnouncementBatchDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


class AnnouncementBatchPublishRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)
    publish: bool = True


@router.post("/announcements/batch-delete", response_model=ApiResponse[DeletedResult])
async def admin_batch_delete_announcements_api(
    req: AnnouncementBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量删除公告（不可恢复）"""
    result = await admin_batch_delete_announcements(db, admin, req.ids)
    return success_response(result, f"已删除 {result['deleted']} 条公告")


@router.post("/announcements/batch-publish", response_model=ApiResponse[AffectedResult])
async def admin_batch_publish_announcements_api(
    req: AnnouncementBatchPublishRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量发布/撤下公告"""
    result = await admin_batch_publish_announcements(db, admin, req.ids, req.publish)
    action = "发布" if req.publish else "撤下"
    return success_response(result, f"已{action} {result['updated']} 条公告")


@router.put("/announcements/{announcement_id}", response_model=ApiResponse[None])
async def admin_update_announcement_api(
    announcement_id: int,
    req: AnnouncementUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新公告"""
    await admin_update_announcement(db, admin, announcement_id, req.model_dump(exclude_unset=True))
    return success_response(message="公告已更新")


@router.delete("/announcements/{announcement_id}", response_model=ApiResponse[None])
async def admin_delete_announcement_api(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除公告"""
    await admin_delete_announcement(db, admin, announcement_id)
    return success_response(message="公告已删除")
