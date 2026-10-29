"""短信 Provider 抽象：业务只依赖 send_code，不关心腾讯/阿里云的签名细节。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SmsError(Exception):
    """短信发送失败（配置缺失 / 上游拒绝 / 网络异常）；API 层统一映射为 502。"""


class SmsProvider(ABC):
    name: str = ""

    @abstractmethod
    async def send_code(self, phone: str, code: str, *, ttl_minutes: int = 5) -> None:
        """发送验证码；失败抛 SmsError。"""
