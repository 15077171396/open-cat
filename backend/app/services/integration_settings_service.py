"""登录集成配置：后台 DB 优先，环境变量兜底。

设计取向与 ai_config_service 一致：
- **只缓存原始值**（密钥在库里是密文），解密只发生在 `secret()` 调用处；
  这样 Redis AOF / 备份里不会出现明文私钥。
- 一次查询取回全部键（这些配置在每个登录请求上都要用），Redis 60s + 进程内 30s。
- DB 故障时回退默认值/环境变量，绝不因为配置层故障把登录打成 500。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.crypto import decrypt_secret
from app.core.degrade import degrade_guard
from app.database import async_session
from app.models.system_setting import SystemSetting
from app.redis import redis_client
from app.services.content_cache import shape_key

CACHE_KEY = shape_key("integration_cfg")
CACHE_TTL = 60
LOCAL_TTL = 30

AUTH_KEYS = (
    # 登录图形验证码开关：enforce_login_captcha 与 /auth/capabilities 共同消费
    "login_captcha_enabled",
    # 登录时段限制（2026-11-19）：core/login_time.check_login_time 消费
    "login_time_window",
    # 会话有效期策略（2026-11-19）：TTL 与绝对上限经 runtime_config 覆盖
    # settings（JWT_* / SESSION_MAX_DAYS），由 create_token_pair / refresh 链消费
    "session_access_minutes",
    "session_refresh_days",
    "session_max_days",
    # 登录失败告警阈值（2026-11-19）：admin_todo_service 的待办联动消费
    "login_fail_alert_threshold",
)

ALL_KEYS = tuple(dict.fromkeys(AUTH_KEYS + ("site_base_url",)))

SECRET_KEYS = frozenset(set())

_DEFAULTS: dict[str, str] = {
    # 默认关闭：内容 API 的交付面包含第三方自建登录页，开关打开前它们不会渲染验证码框
    "login_captcha_enabled": "0",
    "site_base_url": "",
}

# 进程内二级缓存：(时间戳, values)；TTL 内直接命中，省一次 Redis 往返
_LOCAL_CACHE: tuple[float, dict[str, str]] | None = None


@dataclass(frozen=True)
class IntegrationConfig:
    """一次请求内使用的配置快照；密钥仍是密文，用 secret() 按需解密。"""

    values: dict[str, str]

    def str(self, key: str) -> str:
        return (self.values.get(key) or _DEFAULTS.get(key) or "").strip()

    def bool(self, key: str) -> bool:
        return self.str(key).lower() in ("1", "true", "on", "yes")

    def int(self, key: str, *, default: int = 0) -> int:
        raw = self.str(key)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def secret(self, key: str) -> str:
        """取密钥明文；解密失败 / 未配置返回空串（调用方决定是否 503）。"""
        raw = self.values.get(key) or ""
        if not raw:
            return ""
        return decrypt_secret(raw) or ""

    @property
    def site_base_url(self) -> str:
        return self.str("site_base_url").rstrip("/")


def _merge_defaults(stored: dict[str, str]) -> dict[str, str]:
    values = dict(_DEFAULTS)
    values.update({key: value for key, value in stored.items() if value is not None})
    return values


async def _cache_get() -> dict[str, str] | None:
    raw = None
    with degrade_guard("integration_settings.cache_get", detail="读取集成配置缓存失败"):
        raw = await redis_client.get(CACHE_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict):
        return {str(key): str(value) for key, value in data.items() if value is not None}
    return None


async def _cache_set(values: dict[str, str]) -> None:
    with degrade_guard(
        "integration_settings.cache_set",
        detail="写入集成配置缓存失败（降级为每次回源 DB）",
    ):
        await redis_client.set(CACHE_KEY, json.dumps(values, ensure_ascii=False), ex=CACHE_TTL)


async def get_config() -> IntegrationConfig:
    global _LOCAL_CACHE
    now = time.monotonic()
    if _LOCAL_CACHE is not None and now - _LOCAL_CACHE[0] < LOCAL_TTL:
        return IntegrationConfig(_LOCAL_CACHE[1])

    cached = await _cache_get()
    if cached is not None:
        values = _merge_defaults(cached)
        _LOCAL_CACHE = (now, values)
        return IntegrationConfig(values)

    stored: dict[str, str] = {}
    with degrade_guard(
        "integration_settings.load",
        detail="读取登录配置失败，回退默认值与环境变量",
    ):
        async with async_session() as session:
            rows = (
                await session.scalars(
                    select(SystemSetting).where(SystemSetting.setting_key.in_(list(ALL_KEYS)))
                )
            ).all()
        stored = {row.setting_key: row.value or "" for row in rows}
        await _cache_set(stored)

    values = _merge_defaults(stored)
    _LOCAL_CACHE = (now, values)
    return IntegrationConfig(values)


async def invalidate_integration_settings_cache() -> None:
    global _LOCAL_CACHE
    _LOCAL_CACHE = None
    with degrade_guard(
        "integration_settings.invalidate",
        detail="Redis 不可用，登录配置缓存未主动失效（将随 60s TTL 过期）",
    ):
        await redis_client.delete(CACHE_KEY)


async def get_auth_config() -> IntegrationConfig:
    return await get_config()


def auth_capabilities(config: IntegrationConfig) -> dict[str, Any]:
    """登录页需要的非敏感能力位（绝不返回任何凭据）。"""
    return {
        "captcha_enabled": config.bool("login_captcha_enabled"),
    }
