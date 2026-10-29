"""支付 Provider 抽象：业务只依赖统一的下单 / 查询 / 回调解析接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


class PaymentError(Exception):
    """支付渠道错误（参数缺失 / 上游拒绝 / 验签失败）；API 层映射为 400/502。"""


@dataclass
class PaymentIntent:
    """下单结果：H5/扫码地址或二维码内容"""

    pay_url: str | None = None
    qr_content: str | None = None
    provider_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentNotification:
    """统一后的回调结果"""

    order_no: str
    trade_no: str | None
    amount_cents: int | None
    success: bool
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(ABC):
    channel: str = ""

    @abstractmethod
    async def create_payment(self, order: Any) -> PaymentIntent:
        ...

    @abstractmethod
    async def query_order(self, order: Any) -> PaymentNotification | None:
        """返回 None 表示仍待支付；成功返回 success=True 的通知。"""

    async def parse_notify(
        self,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
    ) -> PaymentNotification:
        raise PaymentError("该渠道不支持异步通知")
