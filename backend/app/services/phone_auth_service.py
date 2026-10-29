"""手机号验证码：发送、频控、一次性校验。

与邮箱验证码（utils/verification.py）分开命名空间：
- 邮箱注册沿用旧 key；
- 手机号登录/绑定用 `verify_code:phone:{scene}:{phone}`，避免「登录验证码被拿去注册/绑定」。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from loguru import logger
from redis.exceptions import RedisError

from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.redis import redis_client
from app.services.integration_settings_service import get_auth_config
from app.services.sms import SmsError, send_sms_code
from app.utils.verification import generate_code, is_phone, mask_account

CODE_PREFIX = "verify_code:phone:"
INTERVAL_PREFIX = "phone_interval:"
FAIL_PREFIX = "phone_fail:"
LOCK_PREFIX = "phone_lock:"
DAILY_PREFIX = "phone_daily:"

MAX_VERIFY_ATTEMPTS = 5
VERIFY_LOCK_SECONDS = 600
VALID_SCENES = ("login", "bind")


def _scene(scene: str) -> str:
    if scene not in VALID_SCENES:
        raise AppException(code=400, message="验证码场景无效")
    return scene


def _code_key(scene: str, phone: str) -> str:
    return f"{CODE_PREFIX}{scene}:{phone}"


def _interval_key(scene: str, phone: str) -> str:
    return f"{INTERVAL_PREFIX}{scene}:{phone}"


def _fail_key(scene: str, phone: str) -> str:
    return f"{FAIL_PREFIX}{scene}:{phone}"


def _lock_key(scene: str, phone: str) -> str:
    return f"{LOCK_PREFIX}{scene}:{phone}"


def _daily_key(phone: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{DAILY_PREFIX}{phone}:{day}"


async def send_phone_code(phone: str, scene: str) -> dict:
    """发送手机验证码；返回 {success, message}，失败抛 AppException。"""
    scene = _scene(scene)
    if not is_phone(phone):
        raise AppException(code=400, message="请输入正确的手机号")

    config = await get_auth_config()
    if not config.bool("phone_login_enabled"):
        raise AppException(code=403, message="手机号登录未开启")

    ttl = max(60, min(config.int("sms_code_ttl_seconds", default=300), 1800))
    interval = max(10, min(config.int("sms_send_interval_seconds", default=60), 600))
    daily_limit = max(1, min(config.int("sms_daily_limit_per_phone", default=10), 100))

    interval_key = _interval_key(scene, phone)
    if not await redis_client.set(interval_key, "1", nx=True, ex=interval):
        ttl_left = await redis_client.ttl(interval_key)
        raise AppException(
            code=429, message=f"发送过于频繁，请 {max(ttl_left, 0)} 秒后再试"
        )

    daily_key = _daily_key(phone)
    count = await redis_client.incr(daily_key)
    if count == 1:
        await redis_client.expire(daily_key, 86400)
    if count > daily_limit:
        await redis_client.delete(interval_key)
        raise AppException(code=429, message="该手机号今日验证码发送次数已达上限")

    provider = config.str("sms_provider") or "mock"
    code = generate_code()
    if provider == "mock":
        # 本地联调使用固定码；生产环境 factory 会拒绝 mock，不会走到这里
        configured = config.str("sms_mock_code")
        if configured and 4 <= len(configured) <= 6:
            code = configured

    try:
        await send_sms_code(phone, code, ttl_minutes=max(1, ttl // 60))
    except SmsError as exc:
        # 发送失败：归还日配额与频控，允许用户立即重试
        await redis_client.delete(interval_key)
        with degrade_guard("phone_auth.rollback_daily", detail="短信发送失败后归还日配额"):
            await redis_client.decr(daily_key)
        logger.bind(event="phone_code_send_failed", phone=mask_account(phone)).warning(
            f"手机验证码发送失败: {exc}"
        )
        raise AppException(code=502, message=str(exc) or "短信发送失败，请稍后再试")

    await redis_client.set(_code_key(scene, phone), code, ex=ttl)
    await redis_client.delete(_fail_key(scene, phone))
    return {"success": True, "message": "验证码已发送"}


async def _consume_code(key: str, stored: str) -> bool:
    try:
        consumed = await redis_client.getdel(key)
    except RedisError:
        # Redis < 6.2 降级：MULTI 内 GET+DEL，同样原子
        pipe = redis_client.pipeline(transaction=True)
        pipe.get(key)
        pipe.delete(key)
        consumed, _ = await pipe.execute()
    return consumed == stored


async def verify_phone_code(phone: str, scene: str, code: str) -> bool:
    scene = _scene(scene)
    lock_key = _lock_key(scene, phone)
    if await redis_client.exists(lock_key):
        return False

    code_key = _code_key(scene, phone)
    stored = await redis_client.get(code_key)
    if not stored:
        return False

    if not code or not code.isascii() or not secrets.compare_digest(stored, code):
        fail_key = _fail_key(scene, phone)
        failures = await redis_client.incr(fail_key)
        await redis_client.expire(fail_key, VERIFY_LOCK_SECONDS)
        if failures >= MAX_VERIFY_ATTEMPTS:
            await redis_client.delete(code_key)
            await redis_client.set(lock_key, "1", ex=VERIFY_LOCK_SECONDS)
        return False

    if not await _consume_code(code_key, stored):
        return False
    await redis_client.delete(_fail_key(scene, phone))
    return True
