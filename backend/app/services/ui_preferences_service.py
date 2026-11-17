"""界面偏好与锁屏口令服务。

分层约定与其它 admin 业务服务一致：**本模块自持事务**（写审计 + commit），
路由只负责参数与响应，不再出现 ``db.commit``（见 tests/test_arch_guards.py P2-29）。

锁屏失败计数刻意复用登录限流的 Redis 键与阈值（``auth_service`` 的私有 helper），
但作用域换成 ``lock:{user_id}``：既保留「同一 IP + 同一主体 10 次失败锁 15 分钟」
的既有策略，又不会连累账号的真实登录。
"""

from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import (
    create_unlock_token,
    hash_password_async,
    validate_password_length,
    verify_password_async,
)
from app.models.user import User
from app.models.user_ui_preference import UserUiPreference
from app.schemas.ui_preferences import UiPreferences
from app.services.admin_audit_service import record_admin_action
from app.services.auth_service import (
    LOGIN_LOCK_SECONDS,
    clear_credential_failures,
    is_credential_locked,
    record_credential_failure,
)

# 白名单由 Schema 定义：默认值 + 字段名集合即「什么可以存」
DEFAULT_PREFERENCES: dict = UiPreferences().model_dump()
PREFERENCE_KEYS = frozenset(DEFAULT_PREFERENCES)


def sanitize_preferences(raw: object) -> dict:
    """把任意来源的 JSON 收敛成合法偏好：未知字段丢弃、非法类型逐项回落默认值。

    落库值与读取值都走这里，保证库里永远只有白名单字段；手工改库或旧版本残留
    也不会让 GET 500。
    """
    if not isinstance(raw, dict):
        return dict(DEFAULT_PREFERENCES)
    known = {key: value for key, value in raw.items() if key in PREFERENCE_KEYS}
    try:
        return UiPreferences.model_validate({**DEFAULT_PREFERENCES, **known}).model_dump()
    except ValidationError:
        logger.warning("界面偏好存在非法字段，按字段逐一回落默认值")
        valid: dict = {}
        for key, value in known.items():
            try:
                single = UiPreferences.model_validate({key: value})
            except ValidationError:
                continue
            valid[key] = getattr(single, key)
        return {**DEFAULT_PREFERENCES, **valid}


def merge_preferences(base: dict, changes: dict) -> dict:
    """增量合并：只接受白名单字段，未提交项保持原值。"""
    merged = sanitize_preferences(base)
    merged.update({key: value for key, value in changes.items() if key in PREFERENCE_KEYS})
    return sanitize_preferences(merged)


async def _get_row(db: AsyncSession, user_id: int) -> Optional[UserUiPreference]:
    result = await db.execute(
        select(UserUiPreference).where(UserUiPreference.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_preferences(db: AsyncSession, user_id: int) -> tuple[Optional[dict], bool]:
    """返回 (偏好或 None, 是否已设置锁屏口令)。

    偏好为 None 表示「服务端从未保存过」——前端应保留本地值，而不是被空记录覆盖。
    """
    row = await _get_row(db, user_id)
    if row is None:
        return None, False
    has_lock = bool(row.lock_password_hash)
    if not row.preferences:
        return None, has_lock
    return sanitize_preferences(row.preferences), has_lock


async def update_preferences(db: AsyncSession, admin: User, changes: dict) -> dict:
    """单用户单行 upsert：不存在则按默认值 + 增量新建；写审计后提交。"""
    user_id = admin.id
    row = await _get_row(db, user_id)
    if row is None:
        row = UserUiPreference(
            user_id=user_id,
            preferences=merge_preferences(DEFAULT_PREFERENCES, changes),
        )
        db.add(row)
    else:
        row.preferences = merge_preferences(row.preferences or DEFAULT_PREFERENCES, changes)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "ui_preferences_update",
        target_type="user",
        target_id=user_id,
        summary="更新界面偏好",
        detail={"changed_keys": sorted(changes)},
    )
    await db.commit()
    return sanitize_preferences(row.preferences)


async def set_lock_password(db: AsyncSession, admin: User, password: str) -> None:
    """设置/更新锁屏口令（bcrypt 存储；明文不出本函数、不入日志、不进审计）。"""
    user_id = admin.id
    try:
        validate_password_length(password)
    except ValueError as exc:
        raise AppException(code=400, message="口令过长，请控制在 72 字节以内") from exc

    row = await _get_row(db, user_id)
    if row is None:
        row = UserUiPreference(user_id=user_id, preferences=None)
        db.add(row)
    row.lock_password_hash = await hash_password_async(password)
    row.lock_password_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "lock_password_update",
        target_type="user",
        target_id=user_id,
        summary="更新锁屏口令",
        detail={"changed_keys": ["lock_password_hash"]},
    )
    await db.commit()


async def verify_lock_password(
    db: AsyncSession, admin: User, password: str, client_ip: str | None = None
) -> str:
    """校验锁屏口令并返回短期解锁令牌；失败计入登录限流（同一作用域 10 次/15 分钟）。"""
    user_id = admin.id
    row = await _get_row(db, user_id)
    if row is None or not row.lock_password_hash:
        raise AppException(code=400, message="尚未设置锁屏口令")

    # 作用域与真实登录隔离：撞锁屏不会把账号登录锁死
    scope = f"lock:{user_id}"
    if await is_credential_locked(scope, client_ip):
        raise AppException(
            code=429,
            message=f"验证失败次数过多，请 {LOGIN_LOCK_SECONDS // 60} 分钟后再试",
        )

    if not await verify_password_async(password, row.lock_password_hash):
        await record_credential_failure(scope, client_ip)
        raise AppException(code=400, message="锁屏口令错误")

    await clear_credential_failures(scope, client_ip)
    token = create_unlock_token(user_id)
    record_admin_action(
        db,
        admin,
        "lock_password_verify",
        target_type="user",
        target_id=user_id,
        summary="锁屏口令校验通过",
        detail={"expires_in": 300},
    )
    await db.commit()
    return token
