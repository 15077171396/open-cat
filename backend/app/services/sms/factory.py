"""短信 Provider 工厂 + 发送入口。"""

from __future__ import annotations

from app.config import settings
from app.services.integration_settings_service import IntegrationConfig, get_auth_config
from app.services.sms.aliyun import AliyunSmsProvider
from app.services.sms.base import SmsError, SmsProvider
from app.services.sms.mock import MockSmsProvider
from app.services.sms.tencent import TencentSmsProvider
from app.utils.verification import mask_account


async def get_sms_provider(config: IntegrationConfig | None = None) -> SmsProvider:
    config = config or await get_auth_config()
    provider = config.str("sms_provider") or "mock"
    if provider == "mock":
        if settings.APP_ENV.lower() in ("production", "prod"):
            raise SmsError("生产环境未配置真实短信服务商（当前为 Mock）")
        return MockSmsProvider()
    if provider == "tencent":
        return TencentSmsProvider(config)
    if provider == "aliyun":
        return AliyunSmsProvider(config)
    raise SmsError(f"未知的短信服务商: {provider}")


async def send_sms_code(phone: str, code: str, *, ttl_minutes: int = 5) -> None:
    """业务侧统一入口：失败抛 SmsError，由 API 映射为 502。"""
    config = await get_auth_config()
    provider = await get_sms_provider(config)
    await provider.send_code(phone, code, ttl_minutes=ttl_minutes)
    # 只记脱敏手机号与通道，绝不记录验证码
    from loguru import logger

    logger.bind(event="sms_sent", phone=mask_account(phone), provider=provider.name).info(
        f"短信验证码已通过 {provider.name} 发送"
    )
