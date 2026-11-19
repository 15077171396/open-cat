"""访问令牌（PAT）管理路由（2026-11-20）：列表 / 签发 / 吊销。

独立授权面（api_token:view / api_token:manage）：令牌是长期机器凭据，
与 setting:update 这类「配置面」分开 —— 能改配置的人不必能签发凭据。

⚠️ 明文令牌只在签发响应里出现一次（data.token），列表只回前缀。
"""

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import require_elevation, router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.open_api import ApiTokenCreatedData, ApiTokenListData, ApiTokenRevokeData
from app.services import api_token_service


class ApiTokenCreateRequest(BaseModel):
    """签发请求：用途名 + 作用域（白名单子集）+ 有效期（0/空 = 永不过期）"""

    name: str = Field(..., min_length=1, max_length=100, description="用途备注")
    scopes: list[str] | None = Field(default=None, description='作用域子集，如 ["content:read"]；空 = 默认 content:read')
    expires_days: int | None = Field(default=None, ge=1, le=3650, description="有效天数；空/0 = 永不过期")


@router.get("/api-tokens", response_model=ApiResponse[ApiTokenListData])
async def admin_list_api_tokens(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """令牌清单（脱敏：只有前缀）。"""
    return success_response({"items": await api_token_service.list_tokens(db)})


@router.post("/api-tokens", response_model=ApiResponse[ApiTokenCreatedData])
async def admin_create_api_token(
    req: ApiTokenCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """签发令牌。**响应里的 token 是明文唯一一次出现**，前端弹窗提示「立即保存」。"""
    result = await api_token_service.create_token(db, admin, req.name, req.scopes, req.expires_days)
    return success_response(result)


@router.delete("/api-tokens/{token_id}", response_model=ApiResponse[ApiTokenRevokeData])
async def admin_revoke_api_token(
    token_id: int,
    _: None = Depends(require_elevation),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """吊销令牌（幂等：重复吊销返回 ok=True 但 revoked=False）。"""
    changed = await api_token_service.revoke_token(db, admin, token_id)
    return success_response({"ok": True, "revoked": changed})
