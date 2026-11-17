"""支付渠道适配层（Stripe Checkout / mock / 微信支付 v3 Native / 支付宝电脑网站）。"""

from app.services.payments.base import PaymentError, PaymentIntent, PaymentNotification, PaymentProvider
from app.services.payments.factory import (
    available_channels,
    default_payment_channel,
    get_payment_provider,
)

__all__ = [
    "PaymentError",
    "PaymentIntent",
    "PaymentNotification",
    "PaymentProvider",
    "available_channels",
    "default_payment_channel",
    "get_payment_provider",
]
