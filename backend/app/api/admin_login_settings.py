"""管理后台 - 登录管理（2026-10-27 从「系统设置 → 登录与短信」抽出）

路由级权限见 core/rbac.py：读 `login_setting:view`，写 `login_setting:update`。

为什么单独开一对接口而不是复用 `/admin/settings`：
1. **权限面**：开关登录方式、填短信凭据是运营/客服的日常动作，不该连带
   「改 COS 密钥 / 支付商户私钥」的写权限（`setting:update` 覆盖全部设置项）；
2. **写范围**：`update_settings(surface=SURFACE_LOGIN)` 只接受登录配置键，
   越界键整批拒绝 —— 收窄的权限码不能写宽范围；
3. **不止是表单**：GET 额外返回各渠道的可用性摘要（开关开着但凭据没配齐 = 点了必失败），
   这是纯设置接口给不出的信息。
"""

from typing import Any, Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import AppException, success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import AffectedResult, SettingsPayload
from app.schemas.common import ApiResponse
from app.services import login_settings_service
from app.services.admin_audit_service import record_admin_action
from app.services.integration_settings_service import invalidate_integration_settings_cache


class LoginSettingsPayload(SettingsPayload):
    """登录管理页载荷：通用设置载荷（definitions / groups / values）+ 渠道可用性摘要"""

    channels: Optional[Any] = None


class LoginSettingsUpdateRequest(BaseModel):
    # 批量更新：{key: value}；value 为 null/空串表示清除该项恢复默认
    settings: dict[str, Optional[str]] = Field(..., min_length=1)


@router.get("/login-settings", response_model=ApiResponse[LoginSettingsPayload])
async def admin_get_login_settings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """登录管理：设置项定义 + 已配置值（密钥为掩码）+ 各渠道可用性摘要"""
    return success_response(await login_settings_service.overview(db))


@router.put("/login-settings", response_model=ApiResponse[AffectedResult])
async def admin_update_login_settings(
    req: LoginSettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量保存登录配置（空值=恢复默认；越界键整批拒绝）"""
    try:
        changed = await login_settings_service.update(db, req.settings)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))
    # 审计：改短信密钥/开关登录方式会直接影响「用户能不能登录」。
    # ⚠️ 与 settings_update 同一口径：只记键名，绝不记值（含短信密钥等凭据）
    record_admin_action(
        db,
        admin,
        "login_setting_update",
        target_type="setting",
        summary=f"更新 {changed} 项登录配置",
        detail={"keys": sorted(req.settings.keys()), "changed": changed},
    )
    # 先 commit 再失效缓存：避免「清缓存后、提交前」的并发读回填旧值
    await db.commit()
    # 登录配置只经 integration_settings_service 读取（60s Redis + 30s 进程内），
    # 该层的键都不在 runtime_config.RUNTIME_KEYS 里，因此无需 apply_runtime_settings
    await invalidate_integration_settings_cache()
    return success_response({"updated": changed}, "登录配置已保存")
