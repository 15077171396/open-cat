"""数据字典管理：字典类型 + 字典项 CRUD。

权限复用 `setting:view / setting:update`；与「系统设置」同一管理层级，不新增权限码。
"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.dictionary import (
    DictItemCreate,
    DictItemRow,
    DictItemUpdate,
    DictTypeCreate,
    DictTypeListData,
    DictTypeRow,
    DictTypeUpdate,
)
from app.services import dictionary_service


@router.get("/dicts", response_model=ApiResponse[DictTypeListData])
async def list_dict_types(
    keyword: str = Query("", max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(await dictionary_service.list_types(db, keyword))


@router.post("/dicts", response_model=ApiResponse[DictTypeRow])
async def create_dict_type(
    payload: DictTypeCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = await dictionary_service.create_type(
        db,
        admin,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        is_enabled=payload.is_enabled,
    )
    return success_response(row)


@router.put("/dicts/{type_id}", response_model=ApiResponse[DictTypeRow])
async def update_dict_type(
    type_id: int,
    payload: DictTypeUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    changes = {
        field: getattr(payload, field)
        for field in payload.model_fields_set
    }
    row = await dictionary_service.update_type(db, admin, type_id, changes)
    return success_response(row)


@router.delete("/dicts/{type_id}", response_model=ApiResponse[ActionAck])
async def delete_dict_type(
    type_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    await dictionary_service.delete_type(db, admin, type_id)
    return success_response({"ok": True})


@router.get("/dicts/{type_id}/items", response_model=ApiResponse[list[DictItemRow]])
async def list_dict_items(
    type_id: int,
    keyword: str = Query("", max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(await dictionary_service.list_items(db, type_id, keyword))


@router.post("/dicts/{type_id}/items", response_model=ApiResponse[DictItemRow])
async def create_dict_item(
    type_id: int,
    payload: DictItemCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = await dictionary_service.create_item(
        db,
        admin,
        type_id,
        label=payload.label,
        value=payload.value,
        sort_order=payload.sort_order,
        is_enabled=payload.is_enabled,
        is_default=payload.is_default,
        remark=payload.remark,
    )
    return success_response(row)


@router.put("/dicts/{type_id}/items/{item_id}", response_model=ApiResponse[DictItemRow])
async def update_dict_item(
    type_id: int,
    item_id: int,
    payload: DictItemUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    changes = {
        field: getattr(payload, field)
        for field in payload.model_fields_set
    }
    row = await dictionary_service.update_item(db, admin, type_id, item_id, changes)
    return success_response(row)


@router.delete("/dicts/{type_id}/items/{item_id}", response_model=ApiResponse[ActionAck])
async def delete_dict_item(
    type_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    await dictionary_service.delete_item(db, admin, type_id, item_id)
    return success_response({"ok": True})
