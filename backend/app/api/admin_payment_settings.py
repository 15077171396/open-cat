"""管理后台 - 支付与渠道（2026-10-27 从「系统设置 → 支付与渠道」抽出）

路由级权限见 core/rbac.py：读 `payment_setting:view`，写 `payment_setting:update`。

与已有的 `admin_payments.py`（充值套餐 / 支付订单）区分：
那个是**业务数据**（运营管理分组），这里是**渠道配置**（系统分组）。

为什么单独开一对接口：
1. **权限面**：改支付商户号/私钥属于资金链路，敏感度不亚于系统设置里的其它密钥，
   但「能不能配支付渠道」与「能不能改 COS 密钥」是两件事，应当能分开授权；
2. **写范围**：`update_settings(surface=SURFACE_PAYMENT)` 只接受支付配置键，越界键整批拒绝；
3. **信息面**：GET 额外返回渠道可用性与风险提示（Mock 是否真实可用、渠道开着但凭据没配齐），
   这两类状态在原表单里完全读不出来，却直接决定「用户能不能付钱」。
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
from app.services import payment_settings_service
from app.services.admin_audit_service import record_admin_action
from app.services.integration_settings_service import invalidate_integration_settings_cache


class PaymentSettingsPayload(SettingsPayload):
    """支付与渠道页载荷：通用设置载荷 + 渠道可用性 + 整页风险提示"""

    enabled: Optional[Any] = None
    env: Optional[Any] = None
    channels: Optional[Any] = None
    warnings: Optional[Any] = None


class PaymentSettingsUpdateRequest(BaseModel):
    # 批量更新：{key: value}；value 为 null/空串表示清除该项恢复默认
    settings: dict[str, Optional[str]] = Field(..., min_length=1)


@router.get("/payment-settings", response_model=ApiResponse[PaymentSettingsPayload])
async def admin_get_payment_settings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """支付与渠道：设置项 + 已配置值（密钥为掩码）+ 渠道可用性 + 风险提示"""
    return success_response(await payment_settings_service.overview(db))


@router.put("/payment-settings", response_model=ApiResponse[AffectedResult])
async def admin_update_payment_settings(
    req: PaymentSettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量保存支付配置（空值=恢复默认；越界键整批拒绝）"""
    try:
        changed = await payment_settings_service.update(db, req.settings)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))
    # 审计：改商户号/私钥/开关直接决定「钱能不能进来」，属高危。
    # ⚠️ 只记键名，绝不记值（含商户私钥、APIv3 密钥）
    record_admin_action(
        db,
        admin,
        "payment_setting_update",
        target_type="setting",
        summary=f"更新 {changed} 项支付配置",
        detail={"keys": sorted(req.settings.keys()), "changed": changed},
    )
    # 先 commit 再失效缓存：支付配置经 integration_settings_service 读取（60s Redis + 30s 进程内），
    # 反序会让并发下单用旧凭据签名，表现为「后台改对了、支付还是验签失败」
    await db.commit()
    await invalidate_integration_settings_cache()
    return success_response({"updated": changed}, "支付配置已保存")
