"""公告路由"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse
from app.services.announcement_service import list_announcements, get_announcement
from app.schemas.content import PageResult
from app.schemas.admin import AnnouncementItem

router = APIRouter()


@router.get("", response_model=ApiResponse[PageResult[AnnouncementItem]])
async def list_announcements_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """已发布公告列表"""
    result = await list_announcements(db, page, page_size)
    return success_response(result)


@router.get("/{announcement_id}", response_model=ApiResponse[AnnouncementItem])
async def get_announcement_api(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
):
    """公告详情"""
    result = await get_announcement(db, announcement_id)
    return success_response(result)
