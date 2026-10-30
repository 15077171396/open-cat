"""认证路由 - 注册/登录/验证码/Token 管理"""

import secrets
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request, Response
from fastapi.responses import RedirectResponse
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
    StarAuthSubmitData,
    StarAuthStatusData,
    TotpStatusData,
    TotpSetupData,
    RecoveryCodesData,
    PhoneCodeRequest,
    PhoneLoginRequest,
    PhoneBindRequest,
    WechatStartData,
    AuthCapabilitiesData,
)
from app.services.auth_service import (
    register,
    login,
    refresh_token,
    logout,
    logout_all,
    build_user_profile,
)
from app.services import totp_service
from app.services.audit_service import submit_auth_request, get_auth_status
from app.services.miniapp_service import miniapp_login, miniapp_bind, miniapp_unbind
from app.services.phone_auth_service import send_phone_code
from app.services.social_auth_service import (
    bind_phone,
    build_wechat_login_url,
    phone_login as phone_login_service,
    wechat_web_login,
)
from app.services.integration_settings_service import auth_capabilities, get_auth_config
from app.utils.verification import send_code
from app.schemas.admin import ActionAck
from app.config import settings as app_settings

router = APIRouter()

# 发送验证码按 IP 日配额：单账号 60 秒频控之外，攻击者可轮换大量目标邮箱把本站
# SMTP 当垃圾邮件中继（短信接入后有 SMS pumping 话费风险）。计数管线在 core/rate_limit.check_ip_daily_quota
_SEND_CODE_IP_DAILY_LIMIT = 20
# 微信扫码 state 与浏览器 cookie 双校验，防「把攻击者微信登录态塞给受害者」的 login CSRF
_WECHAT_STATE_COOKIE = "ai_cat_wechat_state"


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


@router.post("/phone/send-code", response_model=ApiResponse[None])
async def send_phone_verification_code(req: PhoneCodeRequest, request: Request):
    """发送手机号验证码（登录 / 绑定两个场景）"""
    config = await get_auth_config()
    if not config.bool("phone_login_enabled"):
        raise AppException(code=403, message="手机号登录未开启")
    await check_ip_daily_quota(
        request,
        "phone_send_code",
        max(1, config.int("sms_daily_limit_per_ip", default=20)),
        "短信发送次数已达今日上限，请明天再试",
    )
    result = await send_phone_code(req.phone, req.scene)
    return success_response(message=result["message"])


@router.post("/phone/login", response_model=ApiResponse[LoginRegisterData])
async def phone_login_api(
    req: PhoneLoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """手机号 + 验证码登录（未注册且开关开启时自动建号）"""
    result = await phone_login_service(
        db,
        phone=req.phone,
        code=req.code,
        invite_code=req.invite_code,
        totp_code=req.totp_code,
        client_ip=get_client_ip(request),
    )
    set_auth_cookies(response, result["access_token"], result["refresh_token"])
    return success_response(result, "登录成功")


@router.post("/phone/bind", response_model=ApiResponse[UserInfoData])
async def bind_phone_api(
    req: PhoneBindRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """登录后绑定手机号（邮箱账号也可绑定）"""
    profile = await bind_phone(db, current_user, phone=req.phone, code=req.code)
    return success_response(profile, "手机号绑定成功")


@router.get("/wechat/web/start", response_model=ApiResponse[WechatStartData])
async def wechat_web_start(
    response: Response,
    redirect: str | None = Query(None, max_length=500),
):
    """生成微信网站应用扫码登录地址（state 存 Redis + 浏览器 cookie 双校验）"""
    url = await build_wechat_login_url(redirect)
    state = (parse_qs(urlparse(url).query).get("state") or [""])[0]
    if state:
        response.set_cookie(
            key=_WECHAT_STATE_COOKIE,
            value=state,
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=not app_settings.is_dev,
            path="/",
        )
    return success_response({"url": url})


@router.get("/wechat/web/callback")
async def wechat_web_callback(
    request: Request,
    code: str = Query(..., max_length=512),
    state: str = Query(..., max_length=128),
    wechat_state_cookie: str | None = Cookie(None, alias=_WECHAT_STATE_COOKIE),
    db: AsyncSession = Depends(get_db),
):
    """微信扫码回调：验 state（Redis + 浏览器 cookie）→ 换身份 → 设置 httpOnly cookie → 跳回前端完成态页面"""
    config = await get_auth_config()
    frontend = (config.site_base_url or app_settings.SITE_BASE_URL).rstrip("/")
    if not wechat_state_cookie or not secrets.compare_digest(wechat_state_cookie, state):
        target = f"{frontend}/login?{urlencode({'wechat_error': '登录状态校验失败，请重新扫码'})}"
        return RedirectResponse(target, status_code=302)
    try:
        result = await wechat_web_login(
            db, code=code, state=state, client_ip=get_client_ip(request)
        )
    except (AppException, AuthenticationError) as exc:
        target = f"{frontend}/login?{urlencode({'wechat_error': exc.message})}"
        error_response = RedirectResponse(target, status_code=302)
        error_response.delete_cookie(_WECHAT_STATE_COOKIE, path="/")
        return error_response

    target = f"{frontend}/login/wechat?{urlencode({'redirect': result.get('redirect') or '/'})}"
    redirect_response = RedirectResponse(target, status_code=302)
    set_auth_cookies(
        redirect_response, result["access_token"], result["refresh_token"]
    )
    redirect_response.delete_cookie(_WECHAT_STATE_COOKIE, path="/")
    return redirect_response


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

    await logout(user_id, access_token=access_token)
    return success_response(message="已退出登录")


@router.get("/me", response_model=ApiResponse[UserInfoData])
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return success_response(build_user_profile(current_user))


# 知识星球认证


class StarAuthRequest(BaseModel):
    """星球号认证请求"""
    # DB 列为 String(50)，超长输入原先在 flush 抛 DataError 落 500，此处前置校验返回 422
    star_code: str = Field(..., min_length=1, max_length=50)


@router.post("/star/submit", response_model=ApiResponse[StarAuthSubmitData])
async def submit_star_auth(
    req: StarAuthRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交知识星球认证申请"""
    result = await submit_auth_request(db, current_user, req.star_code)
    return success_response(result, "认证申请已提交")


@router.get("/star/status", response_model=ApiResponse[StarAuthStatusData])
async def get_star_auth_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询认证状态"""
    result = await get_auth_status(db, current_user)
    return success_response(result)


# 小程序端登录（微信 / 账号密码）：纯增量，不影响 Web 端链路；
# 响应结构与 Web /auth/login 一致，额外返回 is_new_user / need_bind；
# 小程序无 Cookie 容器，Token 由客户端放入 Authorization 头（pick_token 头优先策略天然适配）


class MiniappLoginRequest(BaseModel):
    """小程序登录请求（三种方式任选其一）"""
    # 方式一：wx.login 的 code（微信静默登录 / 账号登录时补绑）
    code: str | None = Field(None, max_length=128)
    # 方式二：账号密码登录（手机号或邮箱）
    account: str | None = Field(None, max_length=100)
    password: str | None = Field(None, max_length=128)
    # 选填：注册/绑定时携带的邀请码
    invite_code: str | None = Field(None, max_length=16)


class MiniappBindRequest(BaseModel):
    """小程序绑定已有账号请求"""
    code: str = Field(..., min_length=1, max_length=128)
    account: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=128)


@router.post("/miniapp/login", response_model=ApiResponse[ActionAck])
async def miniapp_login_api(
    req: MiniappLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """小程序登录（微信 code 静默登录 / 账号密码登录）；openid 未绑定时返回 need_bind=true，由小程序引导绑定"""
    result = await miniapp_login(
        db,
        code=req.code,
        account=req.account,
        password=req.password,
        invite_code=req.invite_code,
        client_ip=get_client_ip(request),
    )
    return success_response(result, "登录成功")


@router.post("/miniapp/bind", response_model=ApiResponse[ActionAck])
async def miniapp_bind_api(
    req: MiniappBindRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """小程序绑定微信身份到已有账号（账号 + 密码校验通过后写入 openid）"""
    result = await miniapp_bind(
        db,
        code=req.code,
        account=req.account,
        password=req.password,
        client_ip=get_client_ip(request),
    )
    return success_response(result, "绑定成功")


@router.post("/miniapp/unbind", response_model=ApiResponse[ActionAck])
async def miniapp_unbind_api(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """小程序解绑微信身份（保留平台账号，解绑后需重新绑定或使用账号密码登录）"""
    result = await miniapp_unbind(db, current_user)
    return success_response(result, "已解绑微信")


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
