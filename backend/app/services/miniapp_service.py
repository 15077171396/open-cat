"""小程序端服务 - 微信登录 / 账号绑定。只新增能力、不改既有服务行为；
复用 build_user_profile 与 Token 设施保证与 Web 端口径一致。
登录方式：①微信 code 静默登录（未绑定且带账号密码时校验后绑定）②账号密码登录
③验证码登录（复用 Web 端 send-code / register，无需本模块参与）。"""

import hashlib

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException, AuthenticationError
from app.models.user import User
from app.services.auth_service import (
    build_user_profile,
    issue_login_tokens,
    verify_account_password,
)
from app.services.integration_settings_service import get_auth_config

# 微信 code2session 接口（服务端调用，需配置 WECHAT_MINI_APPID / SECRET）
_WX_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

# dev 模式 mock 登录的 openid 前缀。仅在未配置微信密钥且 APP_ENV=development 时可用，
# 生产环境恒不可达（见 _resolve_openid），避免开发期无小程序账号时无法联调。
_DEV_OPENID_PREFIX = "dev_"


def _mask_account(account: str) -> str:
    """账号脱敏（PII 不入日志，与 verification.mask_account 同口径）"""
    if not account:
        return ""
    if "@" in account:
        name, _, domain = account.partition("@")
        return f"{name[:2]}***@{domain}"
    return f"{account[:3]}****{account[-4:]}" if len(account) >= 7 else "***"


async def code_to_session(code: str) -> tuple[str, str | None]:
    """用 wx.login 的 code 换取 (openid, unionid)。

    - 已配置小程序密钥：调用微信 code2session 接口
    - 未配置且为开发环境：返回确定性 mock openid（便于本地联调）
    - 未配置且为生产环境：拒绝并明确提示（不静默降级为假登录）
    """
    appid = getattr(settings, "WECHAT_MINI_APPID", "")
    secret = getattr(settings, "WECHAT_MINI_SECRET", "")

    if not appid or not secret:
        if settings.is_dev:
            # 同一 code 映射同一 openid，便于反复调试；不同 code 得到不同用户
            digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:24]
            logger.warning("未配置 WECHAT_MINI_APPID/SECRET，使用开发环境 mock openid 登录")
            return f"{_DEV_OPENID_PREFIX}{digest}", None
        raise AppException(code=503, message="服务端未配置微信小程序密钥，暂无法使用微信登录")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _WX_CODE2SESSION_URL,
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
        payload = resp.json()
    except Exception as exc:  # 网络异常 / JSON 解析失败
        logger.error(f"微信 code2session 调用失败: {exc}")
        raise AppException(code=502, message="微信登录服务暂不可用，请稍后重试")

    openid = payload.get("openid")
    if not openid:
        # 常见错误：40029 code 无效 / 45011 频率限制 / 40125 密钥错误
        errmsg = payload.get("errmsg", "未知错误")
        logger.warning(f"微信 code2session 返回错误: errcode={payload.get('errcode')} errmsg={errmsg}")
        raise AuthenticationError("微信登录失败，请重试")
    unionid = str(payload["unionid"]) if payload.get("unionid") else None
    return str(openid), unionid


async def code_to_openid(code: str) -> str:
    """兼容旧调用：只取 openid。"""
    openid, _unionid = await code_to_session(code)
    return openid


async def _issue_tokens(db: AsyncSession, user: User) -> dict:
    """与 Web / 手机号 / 微信扫码共用同一套 Token 签发口径。"""
    return await issue_login_tokens(db, user)


async def _find_by_openid(db: AsyncSession, openid: str) -> User | None:
    result = await db.execute(
        select(User).where(User.wechat_openid == openid, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def _find_by_unionid(db: AsyncSession, unionid: str | None) -> User | None:
    if not unionid:
        return None
    result = await db.execute(
        select(User).where(User.wechat_unionid == unionid, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def _find_by_wechat(db: AsyncSession, openid: str, unionid: str | None) -> User | None:
    """unionid 优先：同一开放平台下 PC/小程序/公众号应落到同一个账号。"""
    return await _find_by_unionid(db, unionid) or await _find_by_openid(db, openid)


async def _find_by_account(db: AsyncSession, account: str) -> User | None:
    result = await db.execute(
        select(User).where(User.account == account, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def miniapp_login(
    db: AsyncSession,
    *,
    code: str | None = None,
    account: str | None = None,
    password: str | None = None,
    invite_code: str | None = None,
    client_ip: str | None = None,
) -> dict:
    """小程序登录入口。

    返回 {access_token, refresh_token, token_type, user, is_new_user, need_bind}
    - need_bind=True 表示该微信身份尚未绑定平台账号，前端需引导绑定或账号密码登录
    - client_ip 决定登录失败计数的作用域（见 auth_service._lock_scope）
    """
    auth_config = await get_auth_config()
    mini_enabled = auth_config.bool("wechat_mini_login_enabled")

    # 方式一：账号密码登录（不涉及微信身份）
    if account and password:
        # 与 Web 端 /auth/login 共用同一个校验入口：此前这里直接调
        # verify_password_async，于是失败锁定与账号枚举的时序均衡在这条路径上
        # 整体缺失（第四轮全量审查 P1-50）
        user = await verify_account_password(db, account, password, client_ip=client_ip)

        # 小程序内登录时可顺带补绑微信身份，便于下次静默登录
        if code and mini_enabled:
            try:
                openid, unionid = await code_to_session(code)
                bound = await _find_by_wechat(db, openid, unionid)
                if bound is None and not user.wechat_openid:
                    user.wechat_openid = openid
                    if unionid and not user.wechat_unionid:
                        user.wechat_unionid = unionid
                    try:
                        await db.flush()
                    except IntegrityError:
                        # 竞态：该 openid/unionid 已被其它账号绑定，放弃补绑但登录继续
                        await db.rollback()
                        user = await _find_by_account(db, account)
                        if not user:
                            raise AuthenticationError("账号或密码错误")
            except (AppException, AuthenticationError):
                # 补绑失败不影响账号密码登录本身
                logger.warning("小程序登录补绑微信身份失败，已跳过补绑")

        tokens = await _issue_tokens(db, user)
        logger.info(f"小程序账号密码登录: {_mask_account(user.account)} (id={user.id})")
        return {
            **tokens,
            "user": build_user_profile(user),
            "is_new_user": False,
            "need_bind": False,
        }

    # 方式二：微信 code 登录
    if not code:
        raise AppException(code=400, message="缺少登录凭证")
    if not mini_enabled:
        raise AppException(code=403, message="微信小程序登录未开启")

    openid, unionid = await code_to_session(code)
    user = await _find_by_wechat(db, openid, unionid)

    # 已绑定：直接登录
    if user:
        if unionid and not user.wechat_unionid:
            user.wechat_unionid = unionid
        if not user.wechat_openid:
            user.wechat_openid = openid
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            user = await _find_by_wechat(db, openid, unionid)
            if user is None:
                raise AppException(code=409, message="微信绑定冲突，请重试")
        tokens = await _issue_tokens(db, user)
        logger.info(f"小程序微信登录: id={user.id}")
        return {
            **tokens,
            "user": build_user_profile(user),
            "is_new_user": False,
            "need_bind": False,
        }

    # 未绑定：需要账号密码完成绑定（不自动创建匿名账号，避免与既有账号体系割裂）
    logger.info("小程序微信登录：openid 未绑定任何账号，需引导绑定")
    return {
        "access_token": "",
        "refresh_token": "",
        "token_type": "bearer",
        "user": None,
        "is_new_user": False,
        "need_bind": True,
    }


async def miniapp_bind(
    db: AsyncSession,
    *,
    code: str,
    account: str,
    password: str,
    client_ip: str | None = None,
) -> dict:
    """将微信身份绑定到已有平台账号（账号 + 密码校验通过后写入 openid/unionid）"""
    auth_config = await get_auth_config()
    if not auth_config.bool("wechat_mini_login_enabled"):
        raise AppException(code=403, message="微信小程序登录未开启")

    openid, unionid = await code_to_session(code)

    # 该微信已绑定其它账号 → 拒绝，防账号串号
    bound = await _find_by_wechat(db, openid, unionid)
    if bound:
        if bound.account == account:
            tokens = await _issue_tokens(db, bound)
            return {**tokens, "user": build_user_profile(bound), "need_bind": False}
        raise AppException(code=409, message="该微信已绑定其它账号，请直接使用微信登录")

    # 与登录同样走统一校验入口（P1-50）：绑定端点同样接受账号密码，缺防护即缺防护
    user = await verify_account_password(db, account, password, client_ip=client_ip)

    if user.wechat_openid and user.wechat_openid != openid:
        raise AppException(code=409, message="该账号已绑定其它微信，请联系管理员解绑")

    user.wechat_openid = openid
    if unionid and not user.wechat_unionid:
        user.wechat_unionid = unionid
    try:
        await db.flush()
    except IntegrityError:
        # 并发绑定竞态：唯一索引兜底
        await db.rollback()
        raise AppException(code=409, message="绑定冲突，请重试")

    tokens = await _issue_tokens(db, user)
    logger.info(f"小程序绑定微信成功: {_mask_account(user.account)} (id={user.id})")
    return {**tokens, "user": build_user_profile(user), "need_bind": False}


async def miniapp_unbind(db: AsyncSession, user: User) -> dict:
    """解绑微信身份（保留平台账号）"""
    if not user.wechat_openid:
        return {"unbound": False}
    user.wechat_openid = None
    await db.flush()
    logger.info(f"小程序解绑微信: id={user.id}")
    return {"unbound": True}
