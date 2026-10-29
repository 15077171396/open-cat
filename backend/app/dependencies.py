"""依赖注入 - 获取当前用户、数据库 Session、Redis"""

from fastapi import Cookie, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.request_context import set_current_user_id
from app.core.security import (
    ACCESS_TOKEN_COOKIE,
    decode_token,
    is_access_token_revoked,
)
from app.core.exceptions import AuthenticationError
from app.models.user import User


def _extract_token_from_header(authorization: str | None) -> str | None:
    """从 Authorization: Bearer xxx 头提取 token"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization.split(" ", 1)[1]


def pick_token(authorization: str | None, access_token_cookie: str | None) -> str | None:
    """优先 Authorization 头（admin 前端等 header 模式），回退 httpOnly cookie（主前端）"""
    return _extract_token_from_header(authorization) or access_token_cookie


async def _decode_valid_payload(token: str | None) -> dict | None:
    """解码 access_token 并校验撤销状态（黑名单/版本号），失败返回 None；由 get_current_user / _optional 共用"""
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    if await is_access_token_revoked(payload):
        return None
    return payload


def _payload_sub(payload: dict | None) -> str | None:
    if not payload:
        return None
    return payload.get("sub")


async def _load_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    """按 id 加载未软删除的用户，返回 None 表示不存在"""
    result = await db.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_current_user(
    authorization: str | None = Header(None),
    access_token_cookie: str | None = Cookie(None, alias=ACCESS_TOKEN_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> User:
    """从 JWT 获取当前用户（Authorization 头优先，回退 httpOnly cookie）"""
    token = pick_token(authorization, access_token_cookie)
    # 保留原始错误消息以维持前端兼容
    if not token:
        raise AuthenticationError("缺少认证信息")

    payload = await _decode_valid_payload(token)
    if not payload:
        raise AuthenticationError("认证信息无效或已过期")

    user_id = _payload_sub(payload)
    if not user_id:
        raise AuthenticationError("认证信息无效或已过期")

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise AuthenticationError("Invalid authentication subject")

    user = await _load_user_by_id(db, user_id_int)
    if not user:
        raise AuthenticationError("用户不存在")

    # 供埋点类旁路（浏览量 / 内容归因）读取，避免把 user 透传到每个 service 签名
    set_current_user_id(user.id)
    return user


async def get_current_user_optional(
    authorization: str | None = Header(None),
    access_token_cookie: str | None = Cookie(None, alias=ACCESS_TOKEN_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """可选的用户获取 (游客也可访问的接口用)；token 已吊销时降级为游客"""
    payload = await _decode_valid_payload(pick_token(authorization, access_token_cookie))
    user_id = _payload_sub(payload)
    if not user_id:
        return None

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return None

    user = await _load_user_by_id(db, user_id_int)
    if user is not None:
        set_current_user_id(user.id)
    return user
