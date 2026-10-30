"""管理后台 - 版本与升级信息。

只读：当前代码版本 / 数据库迁移状态 / 可选的最新 GitHub release。
`APP_UPDATE_REPO` 留空时不联网，离线部署也能正常使用。
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import StatsResult
from app.schemas.common import ApiResponse
from app.services.version_service import get_version_info


@router.get("/version", response_model=ApiResponse[StatsResult])
async def version_info(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """版本、迁移与最新 release 检查结果。"""
    return success_response(await get_version_info(db))
