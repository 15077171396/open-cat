"""管理后台 - 邮件通知（2026-10-27 从「系统设置 → 邮件通知」抽出）

路由级权限见 core/rbac.py：读 `mail_setting:view`，写 `mail_setting:update`，
发送测试邮件用 `mail_setting:update`（它是一次**对外动作**，比读敏感）。

除了配置读写，这里多一个 `POST /mail-settings/test`：
SMTP 配错的表现是「用户说收不到验证码」，而管理员在后台只能看到参数输入框 ——
没有任何办法验证「现在到底能不能发出去」。测试邮件用与真实验证码**完全相同**的
连接参数（`utils/mailer.connect_kwargs`），把这段只能靠现象反推的排查变成一次点击。

⚠️ 保存后必须 `apply_runtime_settings()`：SMTP 参数经 runtime_config 注入到
`settings.SMTP_*`，不立即应用的话，本 worker 会在最长 60 秒内仍用旧值 ——
表现为「刚保存完就点测试邮件，报的还是上一个服务器的错」。
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
from app.services import mail_settings_service
from app.services.admin_audit_service import record_admin_action
from app.services.runtime_config import (
    apply_runtime_settings,
    invalidate_runtime_settings_cache,
)


class MailSettingsPayload(SettingsPayload):
    """邮件通知页载荷：通用设置载荷 + 通道状态"""

    channel: Optional[Any] = None


class MailTestResult(SettingsPayload):
    """测试发信的返回（只回显收件地址，不回显任何凭据）"""

    to: Optional[str] = None


class MailSettingsUpdateRequest(BaseModel):
    # 批量更新：{key: value}；value 为 null/空串表示清除该项恢复默认
    settings: dict[str, Optional[str]] = Field(..., min_length=1)


class MailTestRequest(BaseModel):
    to: str = Field(..., min_length=3, max_length=254, description="收件地址")


@router.get("/mail-settings", response_model=ApiResponse[MailSettingsPayload])
async def admin_get_mail_settings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """邮件通知：设置项 + 已配置值（授权码为掩码）+ 通道状态（缺哪几项 / 加密方式）"""
    return success_response(await mail_settings_service.overview(db))


@router.put("/mail-settings", response_model=ApiResponse[AffectedResult])
async def admin_update_mail_settings(
    req: MailSettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量保存邮件通道配置（空值=恢复默认；越界键整批拒绝）"""
    try:
        changed = await mail_settings_service.update(db, req.settings)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))
    # 审计：改错 SMTP = 全站注册与找回密码失效，属高危。
    # ⚠️ 只记键名，不记授权码
    record_admin_action(
        db,
        admin,
        "mail_setting_update",
        target_type="setting",
        summary=f"更新 {changed} 项邮件通道配置",
        detail={"keys": sorted(req.settings.keys()), "changed": changed},
    )
    # 先 commit 再失效/应用：反序会让并发读把旧值回填，或测试邮件用旧服务器
    await db.commit()
    await invalidate_runtime_settings_cache()
    await apply_runtime_settings()
    return success_response({"updated": changed}, "邮件通道配置已保存")


@router.post("/mail-settings/test", response_model=ApiResponse[MailTestResult])
async def admin_send_test_mail(
    req: MailTestRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """发送一封测试邮件（使用与真实验证码相同的连接参数）"""
    try:
        mail_settings_service.check_rate_limit()
    except RuntimeError as exc:
        raise AppException(code=429, message=str(exc))
    try:
        to_email = mail_settings_service.validate_recipient(req.to)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))

    ok, detail = await mail_settings_service.send_test_email(to_email)
    # 成败都记：这条审计的意义正是「谁在什么时候向外发过信」，
    # 只记成功会让「反复试错导致对方网关把域名拉黑」这件事查不到人
    record_admin_action(
        db,
        admin,
        "mail_test_send",
        target_type="setting",
        summary=f"发送测试邮件至 {to_email}（{'成功' if ok else '失败'}）",
        detail={"to": to_email, "ok": ok, "error": "" if ok else detail[:200]},
    )
    # 失败分支也要先落库再抛错：否则事务回滚会把这条审计一起丢掉
    await db.commit()
    if not ok:
        # 失败返回 400 而不是 500：发信失败从来不是程序异常，是配置或网络问题
        raise AppException(code=400, message=f"发送失败：{detail}")
    return success_response({"to": to_email}, "测试邮件已发送，请到收件箱（含垃圾箱）确认")
