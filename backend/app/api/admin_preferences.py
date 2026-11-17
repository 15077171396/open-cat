"""本人界面偏好与锁屏口令路由。

权限口径：四个端点都是「读写我自己」（偏好、本机锁屏），复用 `sessions` 的先例
不设细粒度权限码，但仍必须在 core/rbac.py 规则表登记空权限条目（fail-closed）；
服务层所有查询都按 `current_user.id` 过滤，不接受任何 userId 参数。
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.core.request_context import get_client_ip
from app.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.ui_preferences import (
    LockPasswordPayload,
    LockPasswordRequest,
    LockVerifyPayload,
    LockVerifyRequest,
    PreferencesPayload,
    UiPreferences,
)
from app.services import ui_preferences_service


def _payload(prefs: dict | None, has_lock_password: bool) -> PreferencesPayload:
    return PreferencesPayload(
        exists=prefs is not None,
        preferences=UiPreferences.model_validate(prefs) if prefs else None,
        has_lock_password=has_lock_password,
    )


@router.get("/preferences", response_model=ApiResponse[PreferencesPayload])
async def get_my_preferences(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """读取本人界面偏好；从未保存过时 `exists=false`，前端保留本地值。"""
    prefs, has_lock_password = await ui_preferences_service.get_preferences(
        db, current_user.id
    )
    return success_response(_payload(prefs, has_lock_password))


@router.put("/preferences", response_model=ApiResponse[PreferencesPayload])
async def update_my_preferences(
    payload: UiPreferences,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """增量保存本人界面偏好（未知字段忽略、非法类型 422）。"""
    changes = payload.only_set_fields()
    await ui_preferences_service.update_preferences(db, current_user, changes)

    prefs, has_lock_password = await ui_preferences_service.get_preferences(
        db, current_user.id
    )
    return success_response(_payload(prefs, has_lock_password))


@router.post("/lock/password", response_model=ApiResponse[LockPasswordPayload])
async def set_my_lock_password(
    payload: LockPasswordRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """设置/更新锁屏口令（bcrypt 存储，明文与摘要都不出现在响应和日志里）。"""
    await ui_preferences_service.set_lock_password(db, current_user, payload.password)
    return success_response(LockPasswordPayload(has_lock_password=True), "锁屏口令已保存")


@router.post("/lock/verify", response_model=ApiResponse[LockVerifyPayload])
async def verify_my_lock_password(
    payload: LockVerifyRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """校验锁屏口令并签发 5 分钟解锁令牌；失败复用登录限流（10 次锁 15 分钟）。"""
    token = await ui_preferences_service.verify_lock_password(
        db, current_user, payload.password, get_client_ip()
    )
    return success_response(LockVerifyPayload(unlock_token=token, expires_in=300))
