"""管理后台 - 法律文档（2026-10-27 从「系统设置 → 法律文档」抽出）

路由级权限见 core/rbac.py：读 `legal_doc:view`，写 `legal_doc:update`。

为什么单独开一对接口：
1. **权限面**：改隐私政策/用户协议是对外合规动作，通常只由负责人做，
   不该连带拿到 `setting:update`（那份权限同时能改 COS 密钥、支付商户私钥）；
2. **写范围**：`update_settings(surface=SURFACE_LEGAL)` 只接受三份法务文本，
   越界键整批拒绝；
3. **信息面**：GET 额外返回每份文档的元信息（是否自定义 / 字数 / 最后保存时间 /
   前台展示位置）—— 原表单只有一段 HTML，看不出改的是哪份、是不是系统默认文案。
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
from app.services import legal_documents_service
from app.services.admin_audit_service import record_admin_action
from app.services.setting_service import invalidate_legal_documents_cache


class LegalDocumentsPayload(SettingsPayload):
    """法律文档页载荷：通用设置载荷（definitions / groups / values）+ 文档元信息"""

    documents: Optional[Any] = None


class LegalDocumentsUpdateRequest(BaseModel):
    # 批量更新：{key: value}；value 为 null/空串表示清空、前台回退系统默认文案
    settings: dict[str, Optional[str]] = Field(..., min_length=1)


@router.get("/legal-documents", response_model=ApiResponse[LegalDocumentsPayload])
async def admin_get_legal_documents(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """法律文档：三份正文 + 元信息（自定义 / 字数 / 最后保存时间）"""
    return success_response(await legal_documents_service.overview(db))


@router.put("/legal-documents", response_model=ApiResponse[AffectedResult])
async def admin_update_legal_documents(
    req: LegalDocumentsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """保存法律文档（空值=恢复系统默认文案；越界键整批拒绝）"""
    try:
        changed = await legal_documents_service.update(db, req.settings)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))
    # 审计：法务文本全站可见，被改了什么必须能追溯到人（属高危，见 AUDIT_ACTION_RISK）。
    # ⚠️ 只记键名与字数，不记正文 —— 正文本身几十 KB，且审计表按 365 天保留
    record_admin_action(
        db,
        admin,
        "legal_doc_update",
        target_type="legal_doc",
        summary=f"更新 {changed} 份法律文档",
        detail={
            "keys": sorted(req.settings.keys()),
            "cleared": sorted(k for k, v in req.settings.items() if not v),
            "changed": changed,
        },
    )
    # 先 commit 再失效缓存：法律文档有独立 Redis 缓存（legal_doc:*），
    # 反序会让并发读把旧文本回填进缓存，表现为「保存成功但页脚还是旧文案」
    await db.commit()
    await invalidate_legal_documents_cache()
    return success_response({"updated": changed}, "法律文档已保存")
