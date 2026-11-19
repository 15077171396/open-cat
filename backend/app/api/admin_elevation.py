"""危险操作授权（elevation）路由（2026-11-20）：重新输入密码换 5 分钟短时凭据。

POST /api/v1/admin/elevation —— 任何管理员可调（写审计：谁在什么时候升级了权限）。
成功响应里的 token 只在本次出现；危险端点（DELETE 令牌 / DELETE Webhook /
删用户等）逐请求校验 `X-Elevation-Token` 头。
"""

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.elevation import grant
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.open_api import ElevationData
from app.services.admin_audit_service import record_admin_action


class ElevationRequest(BaseModel):
    """重新输入登录密码"""

    password: str = Field(..., min_length=1, max_length=128)


@router.post("/elevation", response_model=ApiResponse[ElevationData])
async def admin_grant_elevation(
    req: ElevationRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """验证密码、签发 5 分钟短时凭据（一次验证覆盖一组危险操作）。"""
    result = await grant(admin, req.password)
    record_admin_action(
        db, admin, "elevation_granted",
        target_type="user", target_id=admin.id,
        summary="通过密码二次校验，授权危险操作（5 分钟窗口）",
    )
    # 审计行与授权动作无事务依赖（grant 走 Redis 不占会话），随路由显式提交
    await db.commit()
    return success_response(result)
