"""手机号验证码登录 / 微信扫码登录（Web）与绑定。

与 auth_service 的关系：本模块只负责「识别用户」（手机号/微信身份），
Token 签发、TOTP 门禁、VIP 过期降级全部复用 auth_service，避免四条登录路径各写一套。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from loguru import logger
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException, AuthenticationError
from app.core.security import hash_password_async
from app.models.user import User
from app.redis import log_redis_degraded, redis_client
from app.services.auth_service import (
    build_user_profile,
    enforce_totp_if_enabled,
    issue_login_tokens,
)
from app.services.integration_settings_service import IntegrationConfig, get_auth_config
from app.services.invite_service import (
    bind_invite_and_reward,
    find_inviter_by_code,
    get_invite_settings,
)
from app.services.phone_auth_service import verify_phone_code
from app.services.totp_service import is_totp_enabled
from app.utils.verification import is_phone, mask_account

WECHAT_STATE_PREFIX = "wechat_state:"
WECHAT_STATE_TTL = 600
_WX_OAUTH_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
_WX_USERINFO_URL = "https://api.weixin.qq.com/sns/userinfo"
_WX_QR_CONNECT_URL = "https://open.weixin.qq.com/connect/qrconnect"


def _safe_redirect(path: str | None) -> str:
    """只允许站内绝对路径，阻断 open redirect。"""
    value = (path or "/").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


async def _find_by_phone(db: AsyncSession, phone: str) -> User | None:
    result = await db.execute(
        select(User).where(User.phone == phone, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    # 迁移窗口 / 历史账号：account 即手机号
    result = await db.execute(
        select(User).where(User.account == phone, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def _bind_legacy_phone(db: AsyncSession, user: User, phone: str) -> None:
    if user.phone == phone:
        return
    if user.phone:
        raise AppException(code=409, message="该账号已绑定其它手机号")
    user.phone = phone
    user.phone_verified_at = datetime.now(timezone.utc)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppException(code=409, message="该手机号已绑定其它账号")


async def phone_login(
    db: AsyncSession,
    *,
    phone: str,
    code: str,
    invite_code: str | None = None,
    totp_code: str | None = None,
    client_ip: str | None = None,
) -> dict:
    config = await get_auth_config()
    if not config.bool("phone_login_enabled"):
        raise AppException(code=403, message="手机号登录未开启")
    if not is_phone(phone):
        raise AppException(code=400, message="请输入正确的手机号")

    user = await _find_by_phone(db, phone)
    is_new_user = False
    inviter = None
    invite_reward = 0

    if user is None and invite_code and invite_code.strip():
        enabled, invite_reward = await get_invite_settings()
        if enabled:
            inviter = await find_inviter_by_code(db, invite_code)
            if not inviter:
                raise AppException(code=400, message="邀请码无效，请检查后重新填写")

    if not await verify_phone_code(phone, "login", code):
        raise AppException(code=400, message="验证码错误或已过期")

    if user is None:
        if not config.bool("phone_login_auto_register"):
            raise AppException(
                code=403,
                message="该手机号尚未注册，请先使用邮箱登录并在个人中心绑定手机号",
            )
        user = User(
            account=phone,
            phone=phone,
            phone_verified_at=datetime.now(timezone.utc),
            password_hash=await hash_password_async(secrets.token_urlsafe(24)),
            role="user",
            nickname=f"用户{phone[-4:]}",
            free_used_seconds=0,
            free_started_at=datetime.now(timezone.utc),
        )
        db.add(user)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # 并发首登：另一个请求已建号，回退后按已有账号登录
            await db.rollback()
            user = await _find_by_phone(db, phone)
            if user is None:
                raise AppException(code=409, message="账号创建冲突，请重试")
        else:
            is_new_user = True
            if inviter is not None:
                await bind_invite_and_reward(db, inviter, user, invite_reward)
            await db.refresh(user)
    else:
        await _bind_legacy_phone(db, user, phone)

    await enforce_totp_if_enabled(
        db, user, account=phone, totp_code=totp_code, client_ip=client_ip
    )
    tokens = await issue_login_tokens(db, user)
    logger.info(f"手机号登录: {mask_account(phone)} (id={user.id}, new={is_new_user})")
    return {**tokens, "is_new_user": is_new_user}


async def bind_phone(db: AsyncSession, user: User, *, phone: str, code: str) -> dict:
    config = await get_auth_config()
    if not config.bool("phone_login_enabled"):
        raise AppException(code=403, message="手机号功能未开启")
    if not is_phone(phone):
        raise AppException(code=400, message="请输入正确的手机号")
    if not await verify_phone_code(phone, "bind", code):
        raise AppException(code=400, message="验证码错误或已过期")

    occupied = await _find_by_phone(db, phone)
    if occupied is not None and occupied.id != user.id:
        raise AppException(code=409, message="该手机号已绑定其它账号")
    if user.phone and user.phone != phone:
        raise AppException(code=409, message="当前账号已绑定其它手机号")

    user.phone = phone
    user.phone_verified_at = datetime.now(timezone.utc)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppException(code=409, message="该手机号已绑定其它账号")
    await db.commit()
    return build_user_profile(user)


# ---------------- 微信扫码登录 ----------------


def _wechat_redirect_uri(config: IntegrationConfig) -> str:
    configured = config.str("wechat_web_redirect_uri")
    if configured:
        return configured
    base = config.site_base_url or settings.SITE_BASE_URL.rstrip("/")
    return f"{base}/api/v1/auth/wechat/web/callback"


async def build_wechat_login_url(redirect_path: str | None = None) -> str:
    config = await get_auth_config()
    if not config.bool("wechat_web_login_enabled"):
        raise AppException(code=403, message="微信扫码登录未开启")
    appid = config.str("wechat_web_appid")
    secret = config.secret("wechat_web_secret")
    if not appid or not secret:
        raise AppException(code=503, message="微信扫码登录参数未配置")

    state = secrets.token_urlsafe(24)
    try:
        await redis_client.set(
            f"{WECHAT_STATE_PREFIX}{state}",
            json.dumps({"redirect": _safe_redirect(redirect_path)}),
            ex=WECHAT_STATE_TTL,
        )
    except RedisError as exc:
        # 无法保存 state 时不能放行（否则无法校验 CSRF）
        log_redis_degraded("social_auth.wechat_state", exc)
        raise AppException(code=503, message="登录服务暂时不可用，请稍后再试")

    redirect_uri = _wechat_redirect_uri(config)
    params = {
        "appid": appid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "snsapi_login",
        "state": state,
    }
    return f"{_WX_QR_CONNECT_URL}?{urlencode(params)}#wechat_redirect"


async def _consume_wechat_state(state: str) -> dict | None:
    if not state:
        return None
    try:
        raw = await redis_client.getdel(f"{WECHAT_STATE_PREFIX}{state}")
    except RedisError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def _exchange_wechat_code(appid: str, secret: str, code: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                _WX_OAUTH_TOKEN_URL,
                params={
                    "appid": appid,
                    "secret": secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"微信 OAuth 换取 token 失败: {type(exc).__name__}")
        raise AppException(code=502, message="微信登录服务暂不可用，请稍后重试")

    if not data.get("access_token") or not data.get("openid"):
        logger.warning(
            f"微信 OAuth 返回错误: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
        )
        raise AuthenticationError("微信登录失败，请重试")
    return data


async def _fetch_wechat_userinfo(access_token: str, openid: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                _WX_USERINFO_URL,
                params={"access_token": access_token, "openid": openid, "lang": "zh_CN"},
            )
            data = response.json()
            return data if isinstance(data, dict) else {}
    except (httpx.HTTPError, ValueError):
        # 昵称/头像拿不到不影响登录
        return {}


async def _find_by_wechat(
    db: AsyncSession, unionid: str | None, web_openid: str | None
) -> User | None:
    if unionid:
        user = (
            await db.execute(
                select(User).where(User.wechat_unionid == unionid, User.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if user is not None:
            return user
    if web_openid:
        return (
            await db.execute(
                select(User).where(
                    User.wechat_web_openid == web_openid, User.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
    return None


async def wechat_web_login(
    db: AsyncSession, *, code: str, state: str, client_ip: str | None = None
) -> dict:
    config = await get_auth_config()
    if not config.bool("wechat_web_login_enabled"):
        raise AppException(code=403, message="微信扫码登录未开启")
    appid = config.str("wechat_web_appid")
    secret = config.secret("wechat_web_secret")
    if not appid or not secret:
        raise AppException(code=503, message="微信扫码登录参数未配置")

    state_data = await _consume_wechat_state(state)
    if state_data is None:
        raise AppException(code=400, message="登录状态已过期，请重新扫码")

    oauth = await _exchange_wechat_code(appid, secret, code)
    openid = str(oauth.get("openid"))
    unionid = str(oauth["unionid"]) if oauth.get("unionid") else None
    userinfo = await _fetch_wechat_userinfo(str(oauth.get("access_token")), openid)

    user = await _find_by_wechat(db, unionid, openid)
    is_new_user = False

    if user is None:
        if not config.bool("wechat_login_auto_register"):
            raise AppException(
                code=403,
                message="该微信尚未绑定账号，请先使用邮箱/手机号登录并在个人中心绑定微信",
            )
        username = userinfo.get("nickname") or "微信用户"
        user = User(
            account=f"wx_{secrets.token_hex(8)}",
            password_hash=await hash_password_async(secrets.token_urlsafe(24)),
            role="user",
            nickname=str(username)[:50],
            avatar_url=(userinfo.get("headimgurl") or None),
            wechat_unionid=unionid,
            wechat_web_openid=openid,
            free_used_seconds=0,
            free_started_at=datetime.now(timezone.utc),
        )
        db.add(user)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            await db.rollback()
            user = await _find_by_wechat(db, unionid, openid)
            if user is None:
                raise AppException(code=409, message="微信账号创建冲突，请重试")
        else:
            is_new_user = True
            await db.refresh(user)
    else:
        # 补齐缺失的绑定标识：unionid 优先；已被其他账号占用时保持原状并留日志
        if unionid and not user.wechat_unionid:
            user.wechat_unionid = unionid
        if not user.wechat_web_openid:
            user.wechat_web_openid = openid
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            logger.warning("微信绑定标识补写冲突，已保留原账号绑定")

    if is_totp_enabled(user):
        # 微信回调无法在同一个浏览器请求里再收 TOTP；明确引导用户走密码+TOTP，
        # 不能因为「微信已绑定」就静默绕过第二因子。
        raise AppException(code=403, message="该账号已开启二次验证，请使用密码登录")

    tokens = await issue_login_tokens(db, user)
    logger.info(f"微信扫码登录: id={user.id}, new={is_new_user}")
    return {
        **tokens,
        "is_new_user": is_new_user,
        "redirect": _safe_redirect(str(state_data.get("redirect") or "/")),
    }
