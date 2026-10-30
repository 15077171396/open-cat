"""认证相关 Schema"""

from typing import Optional
from pydantic import BaseModel, Field, field_validator

from app.core.security import validate_password_length
from app.utils.verification import ACCOUNT_FORMAT_ERROR, is_email_or_phone


class RegisterRequest(BaseModel):
    """注册请求"""
    # max_length 与 DB 列 String(100) 对齐：超长合法格式邮箱原先在写库时
    # 抛 DataError 落入全局 500，前置校验返回 422
    account: str = Field(..., max_length=100, description="手机号或邮箱")
    password: str = Field(..., min_length=6, max_length=50, description="密码(6-50位)")
    code: str = Field(..., min_length=4, max_length=6, description="验证码")
    invite_code: Optional[str] = Field(None, max_length=16, description="邀请码(选填，通过好友分享链接或邀请码填入)")

    @field_validator("account")
    @classmethod
    def validate_account(cls, v):
        """校验手机号或邮箱格式（正则在 utils.verification，全仓唯一定义点）"""
        if is_email_or_phone(v):
            return v
        raise ValueError(ACCOUNT_FORMAT_ERROR)


    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        validate_password_length(v)
        return v


class LoginRequest(BaseModel):
    """登录请求"""
    # ⚠️ 必须有长度上限（第三轮增量审查 2026-10-11 P1-35）：该字段原先**既无 max_length 也无格式校验**
    # （对比 RegisterRequest.account 两者都有），而 `_record_login_failure` 会用**原始字符串**写 Redis 键
    # `login_fail:{account}` ⇒ 一条 {"account": "<20MB>", "password": "x"} 可以按 IP 限流的速率产出巨量 Redis 键；
    # `maxmemory 512mb` + `noeviction` 一旦写满，`is_access_token_revoked` 是 **fail-open** ——
    # 登出 / 改密 / 角色降级 / 账号删除会**静默变成空操作**，长达 access token 的整个寿命。
    # 上限取 100 而不是随手写个 128：DB 列是 `String(100)`，任何存量账号都不可能超过 100 字符，
    # 所以这个上限**不会把任何现有用户挡在门外**。
    # 刻意**不加**账号格式正则：存量账号可能有历史遗留格式，登录侧加格式校验会把老用户直接锁死
    # （注册侧有正则是因为那是新数据的入口）。
    account: str = Field(..., min_length=1, max_length=100, description="手机号或邮箱")
    # 上限防超长字符串透传给 bcrypt（CPU 放大）；bcrypt 实际仅消费 72 字节，
    # 128 字符上限兼容全中文密码（72 字节 ≈ 24 个中文字符）且远超常规密码长度
    password: str = Field(..., min_length=1, max_length=128, description="密码")
    # TOTP 二次验证码（启用二次验证的账号必填）。
    # 长度上限 12 而不是 6：恢复码形如 ABCDE-FGHJK（11 字符），同一个字段两用。
    totp_code: Optional[str] = Field(None, max_length=12, description="二次验证码或恢复码")


class SendCodeRequest(BaseModel):
    """发送验证码请求"""
    # 上限与 LoginRequest 对齐：account 会被拼进 Redis key（verify_code:interval:{account}），
    # 无上限时可被用来制造超大 key；Redis 是 maxmemory 512mb + noeviction，
    # 一旦写满会让令牌黑名单/版本号校验 fail-open（详见 LoginRequest 的注释）
    account: str = Field(..., max_length=100, description="手机号或邮箱")

    @field_validator("account")
    @classmethod
    def validate_account(cls, v):
        if is_email_or_phone(v):
            return v
        raise ValueError(ACCOUNT_FORMAT_ERROR)


class RefreshTokenRequest(BaseModel):
    """刷新 Token 请求（refresh_token 缺省时后端回退 httpOnly cookie）"""
    refresh_token: Optional[str] = Field(None, description="refresh_token")


class PhoneCodeRequest(BaseModel):
    """手机号验证码发送请求"""
    phone: str = Field(..., min_length=11, max_length=11, description="大陆手机号")
    scene: str = Field("login", pattern="^(login|bind)$", description="login=登录 / bind=绑定")


class PhoneLoginRequest(BaseModel):
    """手机号验证码登录请求"""
    phone: str = Field(..., min_length=11, max_length=11)
    code: str = Field(..., min_length=4, max_length=6)
    invite_code: Optional[str] = Field(None, max_length=16)
    # 已启用 TOTP 的账号仍须第二因子
    totp_code: Optional[str] = Field(None, max_length=12)


class PhoneBindRequest(BaseModel):
    """登录后绑定手机号"""
    phone: str = Field(..., min_length=11, max_length=11)
    code: str = Field(..., min_length=4, max_length=6)


class WechatStartData(BaseModel):
    """微信扫码登录跳转地址"""
    url: str


class AuthCapabilitiesData(BaseModel):
    """登录页能力位（只含是否可用，不含任何凭据）"""

    phone_login: bool = False
    phone_auto_register: bool = False
    wechat_web_login: bool = False
    wechat_mini_login: bool = False
    sms_provider: str = "mock"


# 响应模型


class UserBriefData(BaseModel):
    """用户简要信息 (登录/注册返回的 user 字段)"""
    id: int
    account: str
    role: str
    nickname: Optional[str] = None
    bio: Optional[str] = None
    # 邮箱账号时与 account 相同（账号体系单字段，见 build_user_profile），手机号账号为 None
    email: Optional[str] = None
    phone: Optional[str] = None
    phone_masked: Optional[str] = None
    has_phone: bool = False
    wechat_bound: bool = False
    free_used_seconds: int = 0
    free_remaining_seconds: int = 0
    points: int = 0
    vip_expire_time: Optional[str] = None
    pending_auth_code: Optional[str] = None
    has_pending_auth_code: bool = False
    auth_status: str = "none"
    avatar_url: Optional[str] = None
    created_at: Optional[str] = None


class LoginRegisterData(BaseModel):
    """登录/注册响应数据"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserBriefData
    is_new_user: bool = False


class UserInfoData(BaseModel):
    """当前用户信息 (/me)"""
    id: int
    account: str
    role: str
    nickname: Optional[str] = None
    bio: Optional[str] = None
    # 邮箱账号时与 account 相同（账号体系单字段，见 build_user_profile），手机号账号为 None
    email: Optional[str] = None
    phone: Optional[str] = None
    phone_masked: Optional[str] = None
    has_phone: bool = False
    wechat_bound: bool = False
    free_used_seconds: int = 0
    free_remaining_seconds: int = 0
    points: int = 0
    vip_expire_time: Optional[str] = None
    has_pending_auth_code: bool = False
    auth_status: str = "none"
    avatar_url: Optional[str] = None
    created_at: Optional[str] = None


class StarAuthSubmitData(BaseModel):
    """星球认证提交响应"""
    status: str
    message: Optional[str] = None


class StarAuthStatusData(BaseModel):
    """星球认证状态响应"""
    status: str
    vip_expire_time: Optional[str] = None
    star_code: Optional[str] = None
    created_at: Optional[str] = None
    reject_reason: Optional[str] = None


class TotpStatusData(BaseModel):
    """二次验证状态（**刻意不含密钥**：密钥只在 setup 时返回一次）"""

    enabled: bool = False
    pending: bool = False
    recovery_remaining: int = 0
    recovery_should_regenerate: bool = False
    # 管理员强制 2FA 已开启：前端隐藏/禁用「关闭二次验证」
    required: bool = False


class TotpSetupData(BaseModel):
    """绑定信息：密钥 / otpauth URI / 一次性恢复码"""

    secret: str
    uri: str
    recovery_codes: list[str]


class RecoveryCodesData(BaseModel):
    """重新生成的恢复码（同样只返回一次）"""

    recovery_codes: list[str]
