"""Mock 支付：本地/验收环境走完整下单 → 支付 → 发权益链路，不产生真实资金。"""

from __future__ import annotations

from app.services.integration_settings_service import IntegrationConfig
from app.services.payments.base import PaymentIntent, PaymentProvider


class MockPaymentProvider(PaymentProvider):
    channel = "mock"

    def __init__(self, config: IntegrationConfig):
        self.config = config

    async def create_payment(self, order) -> PaymentIntent:
        base = self.config.site_base_url
        return PaymentIntent(
            pay_url=f"{base}/pay/mock?order_no={order.order_no}",
            provider_payload={"mock": True},
        )

    async def query_order(self, order):
        # Mock 订单只能通过受控的 mock-pay 接口置为已支付
        return None
