"""管理后台 - 系统设置"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import AppException, success_response
from app.schemas.common import ApiResponse
from app.api._admin_shared import router

from app.services.setting_service import (
    SURFACE_SETTINGS,
    definitions_payload,
    groups_payload,
    get_stored_settings,
    update_settings,
    invalidate_public_settings_cache,
    invalidate_legal_documents_cache,
)
from app.services.ai_config_service import invalidate_ai_config_cache
from app.services.ai_usage_service import invalidate_quota_config_cache
from app.services.runtime_config import (
    apply_runtime_settings,
    invalidate_runtime_settings_cache,
)
from app.services.integration_settings_service import invalidate_integration_settings_cache
from app.services.admin_audit_service import record_admin_action
from app.schemas.admin import AffectedResult, SettingsPayload


# 系统设置

class SettingsUpdateRequest(BaseModel):
    # 批量更新：{key: value}；value 为 null/空串表示清除该项恢复默认
    settings: dict[str, Optional[str]] = Field(..., min_length=1)


@router.get("/settings", response_model=ApiResponse[SettingsPayload])
async def admin_get_settings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """系统设置：设置项定义列表（渲染表单）+ 已显式配置的值

    ⚠️ 按 `SURFACE_SETTINGS` 过滤：登录相关分组已抽到「系统 → 登录管理」
    （`/admin/login-settings`）。这里若仍返回它们，同一批开关会出现在两个页面，
    各自保存一次、互相覆盖，而且没有任何报错。
    """
    return success_response({
        "definitions": definitions_payload(surface=SURFACE_SETTINGS),
        # 分组顺序与说明由后端下发（EXT-05）：前端不再维护 GROUP_ORDER，
        # 否则后端新增分组时前端会把整组设置静默丢弃
        "groups": groups_payload(surface=SURFACE_SETTINGS),
        "values": await get_stored_settings(db, surface=SURFACE_SETTINGS),
    })


@router.put("/settings", response_model=ApiResponse[AffectedResult])
async def admin_update_settings(
    req: SettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量保存系统设置（空值=恢复默认）"""
    try:
        changed = await update_settings(db, req.settings, surface=SURFACE_SETTINGS)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))
    # 审计：本端点能改 AI 密钥、base_url、法律文档 HTML，此前是唯一「写敏感数据不留痕」的入口；
    # ⚠️ 只记键名绝不记值——值含 AI Key 等秘密，记值等于把密钥复制到另一份可检索的明文存储
    record_admin_action(
        db,
        admin,
        "settings_update",
        target_type="setting",
        summary=f"更新 {changed} 项系统设置",
        detail={"keys": sorted(req.settings.keys()), "changed": changed},
    )
    # 先 commit 再失效缓存：避免「清缓存后、提交前」的并发读回填旧值
    await db.commit()
    await invalidate_public_settings_cache()
    await invalidate_legal_documents_cache()
    # 登录/短信/支付参数（含公众号/商户凭据）与 AI 配置同口径：保存即失效
    await invalidate_integration_settings_cache()
    # AI Provider 配置也要失效：否则会出现「后台改了 Key 并提示保存成功，
    # 但服务端仍在用旧 Key」——这类问题不报错，只能靠现象反推
    await invalidate_ai_config_cache()
    # AI 护栏的单价/上限同理：不清缓存时，另一个 gunicorn worker 最多 60 秒
    # 仍在用旧阈值与旧单价，表现为「保存成功但看板金额不变」
    await invalidate_quota_config_cache()
    # 运行期配置（免费时长 / 审核 / 限流 / 上传 / SEO / 凭据等）：
    # 先失效缓存，再**立即**应用到当前 worker（其余 worker 由 60s 轮询跟上）
    await invalidate_runtime_settings_cache()
    await apply_runtime_settings()
    return success_response({"updated": changed}, "设置已保存")
