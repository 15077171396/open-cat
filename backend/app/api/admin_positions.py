"""岗位目录管理（组织架构）。权限复用 department:view / department:edit。"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.position import PositionCreate, PositionListData, PositionRow, PositionUpdate
from app.services import position_service


@router.get("/positions", response_model=ApiResponse[PositionListData])
async def list_positions(
    keyword: str = Query("", max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(await position_service.list_positions(db, keyword))


@router.post("/positions", response_model=ApiResponse[PositionRow])
async def create_position(
    payload: PositionCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = await position_service.create_position(
        db,
        admin,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        sort_order=payload.sort_order,
        is_enabled=payload.is_enabled,
    )
    return success_response(row)


@router.put("/positions/{position_id}", response_model=ApiResponse[PositionRow])
async def update_position(
    position_id: int,
    payload: PositionUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    changes = {field: getattr(payload, field) for field in payload.model_fields_set}
    row = await position_service.update_position(db, admin, position_id, changes)
    return success_response(row)


@router.delete("/positions/{position_id}", response_model=ApiResponse[ActionAck])
async def delete_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    await position_service.delete_position(db, admin, position_id)
    return success_response({"ok": True})
