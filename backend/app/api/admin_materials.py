"""管理端素材库：统一浏览本地上传、图库导入与 AI 生成素材。"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.common import KEYWORD_MAX_LENGTH
from app.services import materials_service


@router.get("/materials")
async def list_materials(
    keyword: str = Query("", max_length=KEYWORD_MAX_LENGTH),
    kind: str = Query("", max_length=20),
    source: str = Query("", max_length=20),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(24, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(await materials_service.list_materials(
        db, keyword=keyword, kind=kind, source=source,
        page=page, page_size=page_size,
    ))


@router.get("/materials/stats")
async def material_stats(
    db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin),
):
    return success_response(await materials_service.material_stats(db))


@router.post("/materials/{material_id}/touch")
async def touch_material(
    material_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    asset = await materials_service.touch_material(db, material_id)
    return success_response(materials_service.serialize_material(asset), "已记录使用")
