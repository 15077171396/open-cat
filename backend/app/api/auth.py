"""认证路由 - 注册/登录/验证码/Token 管理"""

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_optional, pick_token
from app.models.user import User
from app.core.exceptions import success_response, AppException, AuthenticationError
from app.core.rate_limit import check_ip_daily_quota, get_client_ip
from app.core.security import (
    ACCESS_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
    clear_auth_cookies,
    decode_token,
    load_refresh_token,
    set_auth_cookies,
)
from app.redis import log_redis_degraded
from app.schemas.common import ApiResponse
from app.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    SendCodeRequest,
    RefreshTokenRequest,
    LoginRegisterData,
    UserInfoData,
    TotpStatusData,
    TotpSetupData,
    RecoveryCodesData,
    AuthCapabilitiesData,
    LoginCaptchaData,
)
from app.services.auth_service import (
    register,
    login,
    refresh_token,
    logout,
    logout_all,
    build_user_profile,
)
from app.services.captcha_service import generate_captcha
from app.services import totp_service
from app.services.integration_settings_service import auth_capabilities, get_auth_config
from app.utils.verification import send_code
from app.schemas.admin import ActionAck

router = APIRouter()

# 发送验证码按 IP 日配额：单账号 60 秒频控之外，攻击者可轮换大量目标邮箱把本站
# SMTP 当垃圾邮件中继。计数管线在 core/rate_limit.check_ip_daily_quota
_SEND_CODE_IP_DAILY_LIMIT = 20


@router.post("/send-code", response_model=ApiResponse[None])
async def send_verification_code(req: SendCodeRequest, request: Request):
    """发送验证码"""
    await check_ip_daily_quota(
        request,
        "send_code",
        _SEND_CODE_IP_DAILY_LIMIT,
        "验证码发送次数已达今日上限，请明天再试",
    )

    result = await send_code(req.account)
    if not result["success"]:
        # 区分错误类型：400 账号格式 / 429 频控 / 502 发送失败
        # （统一 429 会误导前端提示与监控告警）
        raise AppException(code=result.get("status", 400), message=result["message"])
    return success_response(message=result["message"])


@router.get("/capabilities", response_model=ApiResponse[AuthCapabilitiesData])
async def auth_capabilities_api():
    """登录页能力位：只告诉前端哪些入口可用，不泄露任何 AppID/Secret。"""
    return success_response(auth_capabilities(await get_auth_config()))


@router.get("/captcha", response_model=ApiResponse[LoginCaptchaData])
async def get_login_captcha():
    """登录图形验证码（`login_captcha_enabled` 开启时登录必带）。

    答案只进 Redis（一次性消费 + 5 分钟 TTL），SVG 由服务端自绘 —— 前端以
    `<img>` 渲染，不进 DOM。端点在 `SENSITIVE_PATHS` 里：防刷 + Redis 故障
    fail-closed（拿不到存储就不签发）。
    """
    return success_response(await generate_captcha())


@router.post("/register", response_model=ApiResponse[LoginRegisterData])
async def user_register(req: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """注册"""
    result = await register(req, db)
    set_auth_cookies(response, result["access_token"], result["refresh_token"])
    return success_response(result, "注册成功")


@router.post("/login", response_model=ApiResponse[LoginRegisterData])
async def user_login(
    req: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """登录"""
    # client_ip 决定失败计数/锁定的作用域；user_agent 只用于「登录设备」列表展示
    result = await login(
        req,
        db,
        client_ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    set_auth_cookies(response, result["access_token"], result["refresh_token"])
    return success_response(result, "登录成功")


@router.post("/refresh", response_model=ApiResponse[ActionAck])
async def refresh_access_token(
    response: Response,
    req: RefreshTokenRequest | None = None,
    refresh_token_cookie: str | None = Cookie(None, alias=REFRESH_TOKEN_COOKIE),
    db: AsyncSession = Depends(get_db),
):
    """刷新 Token（refresh_token 取请求体，缺省回退 httpOnly cookie）"""
    token = (req.refresh_token if req else None) or refresh_token_cookie
    if not token:
        raise AuthenticationError("缺少 refresh_token")
    tokens = await refresh_token(token, db)
    set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])
    return success_response(tokens, "Token 刷新成功")


class LogoutRequest(BaseModel):
    """登出请求（refresh_token 可选）：允许只带 refresh token 登出——access token 过期后
    get_current_user 直接 401 而前端把登出失败吞掉，服务端吊销必然失败、7 天 refresh token 仍可续期、登出形同虚设"""

    refresh_token: str | None = Field(None, max_length=4096)


async def _user_id_from_refresh_token(refresh_token: str) -> int | None:
    """仅凭 refresh_token 解析出用户 id（供 access token 已过期时的登出使用）；
    必须与 Redis 存的那一枚逐字一致，否则等于开放「凭空吊销他人会话」的探测口子"""
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        return None
    raw_sub = payload.get("sub")
    try:
        user_id = int(raw_sub)
    except (TypeError, ValueError):
        return None
    try:
        stored = await load_refresh_token(user_id, payload.get("sid"))
    except Exception:  # noqa: BLE001 Redis 不可用 → 无法确认，按无效处理
        log_redis_degraded("auth.logout_by_refresh", "Redis 不可用，无法校验 refresh_token")
        return None
    if not stored or stored != refresh_token:
        return None
    return user_id


@router.post("/logout", response_model=ApiResponse[None])
async def user_logout(
    response: Response,
    request: Request,
    payload: LogoutRequest | None = None,
    current_user: User = Depends(get_current_user_optional),
    authorization: str | None = Header(None),
    access_token_cookie: str | None = Cookie(None, alias=ACCESS_TOKEN_COOKIE),
    refresh_token_cookie: str | None = Cookie(None, alias=REFRESH_TOKEN_COOKIE),
):
    """登出（吊销 access_token + 清除认证 cookie + 删除 refresh_token）。
    凭据优先级: access token → 请求体 refresh token → refresh token cookie；
    三者皆无仍返回成功（登出幂等，且不返回 401 以免 token 有效性被当探测面）"""
    # 先清除认证 cookie（无论 Redis 是否可用，浏览器侧立即退出登录态）
    clear_auth_cookies(response)
    # 提取本次请求携带的 access_token 供黑名单吊销（Authorization 头优先，回退 cookie）
    access_token = pick_token(authorization, access_token_cookie)

    user_id = current_user.id if current_user is not None else None
    if user_id is None:
        fallback_refresh = (payload.refresh_token if payload else None) or refresh_token_cookie
        if fallback_refresh:
            user_id = await _user_id_from_refresh_token(fallback_refresh)
    if user_id is None:
        return success_response(message="已退出登录")

    await logout(
        user_id,
        access_token=access_token,
        # 登出事件落登录日志：来源字段与登录/失败同一口径
        client_ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        account=(current_user.account if current_user is not None else ""),
    )
    return success_response(message="已退出登录")


@router.get("/me", response_model=ApiResponse[UserInfoData])
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return success_response(build_user_profile(current_user))


# ---------------- TOTP 二次验证（2026-10-27） ----------------
#
# 语义：密码是「你是谁」，TOTP 是「你手上有没有那台设备」。
# 绑定分两步（setup → enable）是刻意的：先写密钥但保持未启用，
# 用户用 App 验证通过才真正打开 —— 否则密钥没存对就永久登录不进去。


class TotpCodeRequest(BaseModel):
    """启用 / 重生成恢复码时用"""

    code: str = Field(..., min_length=6, max_length=12, description="6 位验证码")


class TotpDisableRequest(BaseModel):
    """关闭二次验证必须验密码：防止「拿到已登录设备的人」直接摘掉第二因子"""

    password: str = Field(..., min_length=1, max_length=128)


@router.get("/totp", response_model=ApiResponse[TotpStatusData])
async def get_totp_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当前账号的二次验证状态（**不返回密钥**）"""
    from app.services import setting_service

    status = totp_service.totp_status(current_user)
    status["required"] = bool(
        current_user.role == "admin"
        and await setting_service.get_bool_setting(db, "admin_require_2fa")
    )
    return success_response(status)


@router.post("/totp/setup", response_model=ApiResponse[TotpSetupData])
async def setup_totp(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """生成绑定信息：密钥、otpauth URI、**一次性**恢复码。

    ⚠️ 恢复码只在这里返回一次（库里只存哈希），务必提示用户当场保存。
    """
    data = await totp_service.setup_totp(db, current_user)
    return success_response(data, "请用验证器 App 扫码，并输入 6 位验证码完成绑定")


@router.post("/totp/enable", response_model=ApiResponse[ActionAck])
async def enable_totp(
    req: TotpCodeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """验证一次验证码后正式启用"""
    await totp_service.enable_totp(db, current_user, req.code)
    return success_response({"enabled": True}, "二次验证已启用")


@router.post("/totp/disable", response_model=ApiResponse[ActionAck])
async def disable_totp(
    req: TotpDisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """关闭二次验证（需密码）"""
    await totp_service.disable_totp(db, current_user, req.password)
    return success_response({"enabled": False}, "二次验证已关闭")


@router.post("/totp/recovery", response_model=ApiResponse[RecoveryCodesData])
async def regenerate_recovery(
    req: TotpCodeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """重新生成恢复码（旧的立即作废，同样只返回一次）"""
    codes = await totp_service.regenerate_recovery_codes(db, current_user, req.code)
    return success_response({"recovery_codes": codes}, "恢复码已重新生成，请立即保存")


@router.post("/logout-all", response_model=ApiResponse[ActionAck])
async def logout_all_devices(
    response: Response,
    current_user: User = Depends(get_current_user),
    authorization: str | None = Header(None),
    access_token_cookie: str | None = Cookie(None, alias=ACCESS_TOKEN_COOKIE),
):
    """退出全部设备（提升令牌版本号 → 所有已签发 token 立即失效）。

    比「只登出当前设备」更保守：怀疑密码泄露时用它一键收口。
    """
    clear_auth_cookies(response)
    await logout_all(current_user.id, pick_token(authorization, access_token_cookie))
    return success_response({"ok": True}, "已退出全部设备")
