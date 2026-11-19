"""IP 黑名单管理路由（2026-11-19）：查看 / 保存。

独立授权面（ip_blacklist:view / ip_blacklist:update）：拉黑与放行直接改变
线上访问面，不该跟 COS 密钥这类 `setting:update` 混在同一个授权里。
"""

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck
from app.schemas.common import ApiResponse
from app.services import ip_blacklist_service


class IpBlacklistSaveRequest(BaseModel):
    """整份覆盖保存：每行一个 IPv4/IPv6 地址或 CIDR 网段"""

    raw_text: str = Field(..., max_length=64 * 1024, description="黑名单文本（换行/逗号分隔）")


@router.get("/ip-blacklist", response_model=ApiResponse[ActionAck])
async def admin_get_ip_blacklist(
    admin: User = Depends(require_admin),
):
    """黑名单清单（有效条目 + 不生效的非法行提示）。"""
    return success_response(ip_blacklist_service.list_entries())


@router.put("/ip-blacklist", response_model=ApiResponse[ActionAck])
async def admin_save_ip_blacklist(
    req: IpBlacklistSaveRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """保存黑名单（整份覆盖）。保存即时生效（本 worker 立即、其余 worker 60 秒内），写审计。"""
    result = await ip_blacklist_service.save_with_audit(db, admin, req.raw_text)
    return success_response(
        {
            "ok": True,
            "changed": result["changed"],
            "entries": result["entries"],
            "invalid_entries": result["invalid_entries"],
            "added": result["added"],
            "removed": result["removed"],
        }
    )
