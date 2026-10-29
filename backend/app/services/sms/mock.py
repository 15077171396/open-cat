"""Mock 短信：不产生真实短信，仅用于本地联调 / 验收。

生产环境由 factory 拒绝使用，避免「以为发了、实际没发」。
"""

from __future__ import annotations

from loguru import logger

from app.config import settings
from app.services.sms.base import SmsError, SmsProvider
from app.utils.verification import mask_account


class MockSmsProvider(SmsProvider):
    name = "mock"

    async def send_code(self, phone: str, code: str, *, ttl_minutes: int = 5) -> None:
        if settings.APP_ENV.lower() in ("production", "prod"):
            raise SmsError("生产环境未配置真实短信服务商（当前为 Mock）")
        logger.bind(event="sms_mock", phone=mask_account(phone), ttl_minutes=ttl_minutes).info(
            "Mock 短信验证码已生成（开发环境）"
        )
