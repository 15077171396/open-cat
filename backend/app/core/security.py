"""安全工具 - 密码哈希 + JWT 生成与校验 + 认证 cookie + 令牌撤销"""

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Response
from passlib.context import CryptContext

from app.config import settings
from app.core.exceptions import AppException
from app.redis import redis_client, log_redis_degraded

# 密码哈希上下文 (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt 最大密码长度 72 字节
MAX_PASSWORD_BYTES = 72

# httpOnly 认证 cookie 名（主前端 cookie 模式；admin 前端仍走 Authorization 头）
ACCESS_TOKEN_COOKIE = "ai_cat_access_token"
REFRESH_TOKEN_COOKIE = "ai_cat_refresh_token"

# 令牌撤销 Redis key 前缀
_BLACKLIST_PREFIX = "token_bl:"
_VERSION_PREFIX = "token_ver:"
# refresh_token 存储前缀（登出/轮换时以其为准校验）
_REFRESH_PREFIX = "refresh_token:"


def _truncate_password(password: str) -> str:
    """截断密码到 bcrypt 支持的最大长度 (72 字节)"""
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        encoded = encoded[:MAX_PASSWORD_BYTES]
    return encoded.decode("utf-8", errors="ignore")


def validate_password_length(password: str) -> None:
    """Reject passwords that bcrypt cannot represent without truncation (first 72 bytes);
    verification keeps the legacy truncation behaviour to avoid locking out existing users."""
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must not exceed {MAX_PASSWORD_BYTES} UTF-8 bytes")


def hash_password(password: str) -> str:
    """密码哈希（同步，仅供脚本/测试使用；async 上下文请用 hash_password_async）"""
    password = _truncate_password(password)
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码（同步，仅供脚本/测试使用；async 上下文请用 verify_password_async）"""
    plain_password = _truncate_password(plain_password)
    return pwd_context.verify(plain_password, hashed_password)


# 异步密码工具：bcrypt 单次运算约 100-300ms，直接在协程中同步调用会挂起整个事件循环，
# 使同进程所有并发请求卡顿，故移入线程池


async def hash_password_async(password: str) -> str:
    """密码哈希（线程池执行，不阻塞事件循环）"""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """校验密码（线程池执行，不阻塞事件循环）"""
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None, ver: int = 0) -> str:
    """生成 access_token（jti 供登出黑名单吊销；ver 为签发时的令牌版本号，改密/注销时递增使存量 token 失效）"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access", "jti": uuid.uuid4().hex, "ver": ver})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """生成 refresh_token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """解码 JWT，返回 payload 或 None"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None


def create_token_pair(user_id: int, role: str, ver: int = 0, sid: str | None = None) -> dict:
    """生成 access + refresh token 对（ver 为当前用户令牌版本号，sid 为会话 id）。

    sid 写进两种 token：access 用它标记「这是哪次登录」，refresh 用它找到自己的
    那一枚 refresh_token（`refresh_token:{uid}:{sid}`）—— 多设备并存靠的就是它。
    sid 缺省时行为与旧版完全一致（单 refresh 键），用于兼容未升级的调用方与旧 token。
    """
    data = {"sub": str(user_id), "role": role}
    if sid:
        data["sid"] = sid
    return {
        "access_token": create_access_token(data, ver=ver),
        "refresh_token": create_refresh_token(data),
        "token_type": "bearer",
    }


# 令牌撤销双机制：黑名单 token_bl:{jti} 登出吊销当前会话（TTL=剩余寿命）；
# 版本号 token_ver:{user_id} 在改密/换邮箱/注销/角色变更时 INCR，使全部存量 token 即刻失效。
# Redis 故障时读路径 fail-open（降级为撤销前行为），写路径仅记日志。

async def get_token_version(user_id: int) -> int:
    """读取用户当前令牌版本号（Redis 故障时返回 0，等价于不校验版本）"""
    try:
        ver = await redis_client.get(f"{_VERSION_PREFIX}{user_id}")
        return int(ver) if ver else 0
    except Exception:
        return 0


async def bump_token_version(user_id: int) -> None:
    """递增用户令牌版本号：吊销该用户全部存量 access_token"""
    try:
        await redis_client.incr(f"{_VERSION_PREFIX}{user_id}")
    except Exception:
        log_redis_degraded("security.bump_token_version", f"递增令牌版本号失败（Redis 不可用），旧 token 在有效期内仍可用: id={user_id}")


async def blacklist_jti(jti: str, ttl_seconds: int | None = None) -> None:
    """按 jti 吊销一枚 access_token。

    两个调用方：登出（`blacklist_access_token` 解析出 jti 与剩余寿命后转调本函数），
    以及「踢掉某个会话」—— 会话记录里存着它当前的 jti，踢的时候直接把那枚拉黑。
    TTL 缺省取 access token 的最长寿命：过了这个时间它本来也过期了。
    """
    if not jti:
        return
    ttl = ttl_seconds if ttl_seconds and ttl_seconds > 0 else settings.JWT_ACCESS_EXPIRE_MINUTES * 60
    try:
        await redis_client.set(f"{_BLACKLIST_PREFIX}{jti}", "1", ex=ttl)
    except Exception:
        log_redis_degraded("security.blacklist_jti", "写入令牌黑名单失败（Redis 不可用）")


async def blacklist_access_token(token: str) -> None:
    """将 access_token 加入黑名单（登出用；token 无效/为 refresh token 时静默跳过）"""
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return
    jti = payload.get("jti")
    if not jti:
        # 旧版本签发的 token 无 jti（升级过渡期），无单会话吊销能力，跳过
        return
    exp = payload.get("exp")
    remaining = int(exp - time.time()) if exp else settings.JWT_ACCESS_EXPIRE_MINUTES * 60
    if remaining <= 0:
        return
    await blacklist_jti(jti, remaining)


async def is_access_token_revoked(payload: dict) -> bool:
    """校验 access_token 是否已被吊销（Redis 故障时 fail-open 放行，不阻断认证主流程）"""
    sub = payload.get("sub")
    if not sub:
        return False
    jti = payload.get("jti")
    token_ver = payload.get("ver", 0)  # 旧版 token 无 ver，按 0 处理（初始版本即 0）
    try:
        pipe = redis_client.pipeline()
        # 旧版 token 无 jti：exists 对空 key 恒为 0，仅剩版本号校验生效
        pipe.exists(f"{_BLACKLIST_PREFIX}{jti or ''}")
        pipe.get(f"{_VERSION_PREFIX}{sub}")
        blacklisted, current_ver = await pipe.execute()
        if blacklisted:
            return True
        return (int(current_ver) if current_ver else 0) != token_ver
    except Exception:
        return False


# refresh_token 读写策略刻意不对称，不要「统一」成同一个：
# 写（store）fail-open：token 已签发，因「记不住」而让注册/登录整体 500 本末倒置；
# 读（load）fail-closed：读不到就无法判断 token 是否已被登出/轮换吊销，放行等于撤销失效，
# 降级为 503 让用户重新登录（access_token 在其有效期内不受影响）。

async def store_refresh_token(user_id: int, token: str, sid: str | None = None) -> None:
    """写入 refresh_token（注册/登录/刷新/小程序登录共用，Redis 故障仅降级不阻断）

    有 sid 时按会话隔离（`refresh_token:{uid}:{sid}`），多设备互不覆盖；
    无 sid 时退回旧键（`refresh_token:{uid}`），仅供未带会话的调用方与旧 token 使用。
    """
    key = f"{_REFRESH_PREFIX}{user_id}:{sid}" if sid else f"{_REFRESH_PREFIX}{user_id}"
    try:
        await redis_client.set(key, token, ex=settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 3600)
    except Exception as exc:
        log_redis_degraded("security.store_refresh_token", exc)


async def load_refresh_token(user_id: int, sid: str | None = None) -> str | None:
    """读取 refresh_token 供校验（Redis 故障时 fail-closed，抛 503 而非放行）

    有 sid 时只认该会话的那一枚；会话被踢掉后 key 已删除，这里自然返回 None。
    无 sid 时读旧键，兼容本次改造前签发的 token。
    """
    key = f"{_REFRESH_PREFIX}{user_id}:{sid}" if sid else f"{_REFRESH_PREFIX}{user_id}"
    try:
        return await redis_client.get(key)
    except Exception as exc:
        log_redis_degraded("security.load_refresh_token", exc)
        raise AppException(code=503, message="服务暂时不可用，请重新登录") from exc


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """将 token 对写入 httpOnly cookie（XSS 无法读取；响应体仍返回 token，admin 前端 header 模式可用）"""
    secure = not settings.is_dev
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        access_token,
        max_age=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        refresh_token,
        max_age=settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def clear_auth_cookies(response: Response) -> None:
    """登出时清除认证 cookie"""
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE)
