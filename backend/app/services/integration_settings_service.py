"""登录 / 短信 / 支付集成配置：后台 DB 优先，环境变量兜底。

设计取向与 ai_config_service 一致：
- **只缓存原始值**（密钥在库里是密文），解密只发生在 `secret()` 调用处；
  这样 Redis AOF / 备份里不会出现明文私钥。
- 一次查询取回全部键（这些配置在每个登录/支付请求上都要用），Redis 60s + 进程内 30s。
- DB 故障时回退默认值/环境变量，绝不因为配置层故障把登录或支付打成 500。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.config import settings
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
    "phone_login_enabled",
    "phone_login_auto_register",
    "wechat_web_login_enabled",
    "wechat_login_auto_register",
    "wechat_mini_login_enabled",
    "wechat_web_appid",
    "wechat_web_secret",
    "wechat_web_redirect_uri",
    "wechat_mini_appid",
    "wechat_mini_secret",
    "sms_provider",
    "sms_code_ttl_seconds",
    "sms_send_interval_seconds",
    "sms_daily_limit_per_phone",
    "sms_daily_limit_per_ip",
    "sms_mock_code",
    "sms_tencent_secret_id",
    "sms_tencent_secret_key",
    "sms_tencent_sdk_app_id",
    "sms_tencent_sign_name",
    "sms_tencent_template_id",
    "sms_tencent_region",
    "sms_tencent_template_params",
    "sms_aliyun_access_key_id",
    "sms_aliyun_access_key_secret",
    "sms_aliyun_sign_name",
    "sms_aliyun_template_code",
    "sms_aliyun_region",
    "sms_aliyun_template_param_key",
)

PAYMENT_KEYS = (
    "payment_enabled",
    "payment_mock_enabled",
    "payment_order_expire_minutes",
    "wechat_pay_enabled",
    "wechat_pay_mch_id",
    "wechat_pay_appid",
    "wechat_pay_serial_no",
    "wechat_pay_api_v3_key",
    "wechat_pay_private_key",
    "wechat_pay_platform_public_key",
    "wechat_pay_notify_url",
    "alipay_enabled",
    "alipay_app_id",
    "alipay_private_key",
    "alipay_public_key",
    "alipay_notify_url",
    "alipay_return_url",
    "alipay_gateway",
)

ALL_KEYS = tuple(dict.fromkeys(AUTH_KEYS + PAYMENT_KEYS + ("site_base_url",)))

SECRET_KEYS = frozenset(
    {
        "wechat_web_secret",
        "wechat_mini_secret",
        "sms_tencent_secret_key",
        "sms_aliyun_access_key_secret",
        "wechat_pay_api_v3_key",
        "wechat_pay_private_key",
        "alipay_private_key",
    }
)

_DEFAULTS: dict[str, str] = {
    "phone_login_enabled": "0",
    "phone_login_auto_register": "1",
    "wechat_web_login_enabled": "0",
    "wechat_login_auto_register": "1",
    "wechat_mini_login_enabled": "0",
    "wechat_web_appid": "",
    "wechat_web_secret": "",
    "wechat_web_redirect_uri": "",
    "wechat_mini_appid": settings.WECHAT_MINI_APPID,
    "wechat_mini_secret": settings.WECHAT_MINI_SECRET,
    "sms_provider": "mock",
    "sms_code_ttl_seconds": "300",
    "sms_send_interval_seconds": "60",
    "sms_daily_limit_per_phone": "10",
    "sms_daily_limit_per_ip": "20",
    "sms_mock_code": "123456",
    "sms_tencent_secret_id": "",
    "sms_tencent_secret_key": "",
    "sms_tencent_sdk_app_id": "",
    "sms_tencent_sign_name": "",
    "sms_tencent_template_id": "",
    "sms_tencent_region": "ap-guangzhou",
    "sms_tencent_template_params": '["{code}","5"]',
    "sms_aliyun_access_key_id": "",
    "sms_aliyun_access_key_secret": "",
    "sms_aliyun_sign_name": "",
    "sms_aliyun_template_code": "",
    "sms_aliyun_region": "cn-hangzhou",
    "sms_aliyun_template_param_key": "code",
    "payment_enabled": "0",
    "payment_mock_enabled": "1",
    "payment_order_expire_minutes": "30",
    "wechat_pay_enabled": "0",
    "wechat_pay_mch_id": "",
    "wechat_pay_appid": "",
    "wechat_pay_serial_no": "",
    "wechat_pay_api_v3_key": "",
    "wechat_pay_private_key": "",
    "wechat_pay_platform_public_key": "",
    "wechat_pay_notify_url": "",
    "alipay_enabled": "0",
    "alipay_app_id": "",
    "alipay_private_key": "",
    "alipay_public_key": "",
    "alipay_notify_url": "",
    "alipay_return_url": "",
    "alipay_gateway": "https://openapi.alipay.com/gateway.do",
    "site_base_url": settings.SITE_BASE_URL,
}

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


def _fallback_values() -> dict[str, str]:
    values = dict(_DEFAULTS)
    # 小程序旧配置仍可从 env 兜底，避免升级后未去后台重填就失效
    values["wechat_mini_appid"] = settings.WECHAT_MINI_APPID or values["wechat_mini_appid"]
    values["wechat_mini_secret"] = settings.WECHAT_MINI_SECRET or values["wechat_mini_secret"]
    return values


def _merge_defaults(stored: dict[str, str]) -> dict[str, str]:
    values = _fallback_values()
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
        detail="读取登录/支付配置失败，回退默认值与环境变量",
    ):
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(SystemSetting).where(SystemSetting.setting_key.in_(ALL_KEYS))
                )
            ).scalars().all()
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
        detail="Redis 不可用，登录/支付配置缓存未主动失效（将随 60s TTL 过期）",
    ):
        await redis_client.delete(CACHE_KEY)


async def get_auth_config() -> IntegrationConfig:
    return await get_config()


async def get_payment_config() -> IntegrationConfig:
    return await get_config()


def auth_capabilities(config: IntegrationConfig) -> dict[str, Any]:
    """登录页需要的非敏感能力位（绝不返回 AppID/Secret/短信凭据）。"""
    return {
        "phone_login": config.bool("phone_login_enabled"),
        "phone_auto_register": config.bool("phone_login_auto_register"),
        "wechat_web_login": config.bool("wechat_web_login_enabled")
        and bool(config.str("wechat_web_appid") and config.secret("wechat_web_secret")),
        "wechat_mini_login": config.bool("wechat_mini_login_enabled")
        and bool(config.str("wechat_mini_appid") and config.secret("wechat_mini_secret")),
        "sms_provider": config.str("sms_provider"),
    }


def payment_capabilities(config: IntegrationConfig) -> dict[str, Any]:
    return {
        "enabled": config.bool("payment_enabled"),
        "mock": config.bool("payment_mock_enabled"),
        "wechat": config.bool("wechat_pay_enabled"),
        "alipay": config.bool("alipay_enabled"),
    }
