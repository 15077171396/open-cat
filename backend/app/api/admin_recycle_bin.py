"""管理后台 - 统一回收站。

三个类型（教程 / 评论 / 用户）各有独立的查看与恢复权限：
- 汇总接口只返回当前管理员有查看权限的类型；
- 列表/恢复路径按类型显式登记权限（fail-closed 规则表），不把权限判断藏在 query 参数里。
"""

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.data_scope import resolve_data_scope
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import RecycleBinItem, RestoreResult, StatsResult
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import PageResult
from app.services import recycle_bin_service


class RecycleRestoreRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.get("/recycle-bin/summary", response_model=ApiResponse[StatsResult])
async def recycle_bin_summary(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """各类型回收站计数（只含有查看权限的类型）。"""
    scope = await resolve_data_scope(db, admin)
    return success_response(await recycle_bin_service.get_summary(db, admin, scope))


@router.get("/recycle-bin/tutorials", response_model=ApiResponse[PageResult[RecycleBinItem]])
async def recycle_bin_tutorials(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(
        await recycle_bin_service.list_tutorials(db, admin, page, page_size, keyword)
    )


@router.post("/recycle-bin/tutorials/restore", response_model=ApiResponse[RestoreResult])
async def recycle_bin_restore_tutorials(
    req: RecycleRestoreRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(
        await recycle_bin_service.restore_tutorials(db, req.ids, admin),
        "教程已恢复",
    )


@router.get("/recycle-bin/comments", response_model=ApiResponse[PageResult[RecycleBinItem]])
async def recycle_bin_comments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(
        await recycle_bin_service.list_comments(db, admin, page, page_size, keyword)
    )


@router.post("/recycle-bin/comments/restore", response_model=ApiResponse[RestoreResult])
async def recycle_bin_restore_comments(
    req: RecycleRestoreRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(
        await recycle_bin_service.restore_comments(db, req.ids, admin),
        "评论已恢复",
    )


@router.get("/recycle-bin/users", response_model=ApiResponse[PageResult[RecycleBinItem]])
async def recycle_bin_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    scope = await resolve_data_scope(db, admin)
    return success_response(
        await recycle_bin_service.list_users(db, admin, page, page_size, keyword, scope)
    )


@router.post("/recycle-bin/users/restore", response_model=ApiResponse[RestoreResult])
async def recycle_bin_restore_users(
    req: RecycleRestoreRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(
        await recycle_bin_service.restore_users(db, req.ids, admin),
        "用户已恢复",
    )
