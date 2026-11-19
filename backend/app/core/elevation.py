"""危险操作二次校验（elevation，2026-11-20，对标文档 S5）。

对标 RuoYi 的思路：删用户 / 改权限 / 吊销令牌这类不可逆动作，前端弹窗只防手滑，
防不了「管理员离开座位后被冒用」。这里要求**重新输入密码**换一张 5 分钟有效的
短时凭据（Redis 单槽：同一管理员最新一次验证覆盖上一次），危险请求必须携带它 ——
后端 `require_elevation` 依赖逐请求校验，伪造头拿不到 token 就是 403。

为什么放 Redis 而不是签名：吊销语义要「改密即失效」可追 —— Redis TTL 到期自动
过期，且多 worker 天然共享；签名方案还得再引一条吊销链，得不偿失。
"""

from __future__ import annotations

import hmac
import secrets

from redis.exceptions import RedisError

from app.core.exceptions import AppException
from app.core.security import verify_password_async
from app.redis import redis_client

_TTL_SECONDS = 300  # 5 分钟窗口：一次验证够连做完一组危险操作

_ELEVATION_HEADER = "X-Elevation-Token"


def _key(admin_id: int) -> str:
    return f"elevate:{admin_id}"


def elevation_header() -> str:
    """请求头名（前端与依赖两侧同源引用）"""
    return _ELEVATION_HEADER


async def grant(admin, password: str) -> dict:
    """校验密码并签发短时凭据。密码错误 → 400（与登录侧文案区分开）。"""
    ok = await verify_password_async(password, admin.password_hash)
    if not ok:
        raise AppException(code=400, message="密码不正确，无法授权危险操作")
    token = secrets.token_hex(16)
    try:
        await redis_client.set(_key(admin.id), token, ex=_TTL_SECONDS)
    except (ConnectionError, TimeoutError, RedisError) as exc:  # Redis 故障时宁可让危险操作做不了
        raise AppException(code=503, message="授权服务暂不可用，请稍后再试") from exc
    return {"token": token, "expires_in": _TTL_SECONDS}


async def verify(admin_id: int, token: str | None) -> bool:
    """逐请求校验：槽为空 / 不匹配 → False。比较用常数时间（token 是秘密值）。"""
    if not token:
        return False
    try:
        stored = await redis_client.get(_key(admin_id))
    except (ConnectionError, TimeoutError, RedisError):  # Redis 故障按未授权处理（fail-closed）
        return False
    if not stored:
        return False
    return hmac.compare_digest(str(stored).encode("utf-8"), token.encode("utf-8"))


def remaining_seconds(now: float, granted_at: float) -> int:
    """纯函数：缓存侧计算剩余有效期（便于前端决定是否复用已缓存的凭据）。"""
    left = _TTL_SECONDS - int(now - granted_at)
    return max(0, left)
