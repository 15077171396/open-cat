"""管理后台 - 备份管理（只读）。

列表只暴露元数据；下载是数据外带，要求 `setting:update` 并写审计。
恢复不在这里执行：宿主机脚本需要 docker / mysqldump / 交互确认，Web 端只给命令与警告。
"""

from fastapi import Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import StatsResult
from app.schemas.common import ApiResponse
from app.services import backup_service, rbac_service


@router.get("/backups", response_model=ApiResponse[StatsResult])
async def list_backups(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """备份目录与文件元数据。目录不可见时 visible=False（本地开发属正常）。"""
    rbac = await rbac_service.get_user_rbac(db, admin.id)
    can_download = await rbac_service.is_super_admin(rbac) or "setting:update" in set(
        rbac.get("perms") or []
    )
    return success_response(await backup_service.list_backups(can_download=can_download))


@router.get("/backups/{name}/download")
async def download_backup(
    name: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """下载备份文件。每次下载写一条 data_export 审计（target_type=backup）。"""
    target = await backup_service.prepare_download(db, admin, name)
    return FileResponse(
        path=str(target),
        media_type="application/gzip",
        filename=target.name,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
