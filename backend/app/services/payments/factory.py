"""支付渠道工厂：渠道是配置数据，业务不写 if/else 分支。"""

from __future__ import annotations

from app.config import settings
from app.services.integration_settings_service import (
    SECRET_KEYS,
    IntegrationConfig,
    get_payment_config,
)
from app.services.payments.alipay import AlipayProvider
from app.services.payments.base import PaymentError, PaymentProvider
from app.services.payments.mock import MockPaymentProvider
from app.services.payments.wechat import WechatPayProvider

#: 各渠道的**必需配置**（唯一事实来源）。
#:
#: 管理端「支付与渠道」页的可用性摘要（`payment_settings_service`）与下单时的
#: `available_channels` 都从这里派生 —— 否则两处清单会各自演化，出现
#: 「后台显示渠道可用、用户点支付报 400」这种谁都不算错的 bug。
_CHANNEL_REQUIRED: dict[str, tuple[str, ...]] = {
    # 注意不含 notify_url：它留空时会回退到 site_base_url + 固定路径（见微信 Provider）
    "wechat": (
        "wechat_pay_mch_id",
        "wechat_pay_appid",
        "wechat_pay_serial_no",
        "wechat_pay_api_v3_key",
        "wechat_pay_private_key",
        "wechat_pay_platform_public_key",
    ),
    "alipay": (
        "alipay_app_id",
        "alipay_private_key",
        "alipay_public_key",
    ),
}

#: 渠道中文名（管理端展示与错误提示共用）
CHANNEL_LABELS: dict[str, str] = {
    "mock": "模拟支付（演示）",
    "wechat": "微信扫码支付",
    "alipay": "支付宝",
}


def _is_configured(config: IntegrationConfig, key: str) -> bool:
    """是否已配置；密钥类走 secret()（库里存密文，直接读是密文串，判断不出「有没有」）"""
    return bool(config.secret(key)) if key in SECRET_KEYS else bool(config.str(key))


def missing_requirements(channel: str, config: IntegrationConfig) -> list[str]:
    """该渠道还缺哪些必需配置（空列表 = 配齐了）"""
    return [key for key in _CHANNEL_REQUIRED.get(channel, ()) if not _is_configured(config, key)]


def mock_allowed(config: IntegrationConfig) -> bool:
    """Mock 支付是否**真的**可用

    ⚠️ 两个条件缺一不可：开关打开 **且** 不是生产环境。生产环境即便把开关打开，
    这里也一律拒绝 —— 否则任何人都能「不付钱直接到账」。
    """
    return config.bool("payment_mock_enabled") and settings.APP_ENV.lower() not in (
        "production",
        "prod",
    )


def wechat_ready(config: IntegrationConfig) -> bool:
    return config.bool("wechat_pay_enabled") and not missing_requirements("wechat", config)


def alipay_ready(config: IntegrationConfig) -> bool:
    return config.bool("alipay_enabled") and not missing_requirements("alipay", config)


def available_channels(config: IntegrationConfig) -> list[dict]:
    """前端渠道选择器：只返回可用渠道，不暴露任何凭据。"""
    if not config.bool("payment_enabled"):
        return []
    channels = []
    if mock_allowed(config):
        channels.append({"value": "mock", "label": CHANNEL_LABELS["mock"]})
    if wechat_ready(config):
        channels.append({"value": "wechat", "label": CHANNEL_LABELS["wechat"]})
    if alipay_ready(config):
        channels.append({"value": "alipay", "label": CHANNEL_LABELS["alipay"]})
    return channels


async def get_payment_provider(
    channel: str,
    config: IntegrationConfig | None = None,
    *,
    require_enabled: bool = True,
) -> PaymentProvider:
    config = config or await get_payment_config()
    if require_enabled and not config.bool("payment_enabled"):
        raise PaymentError("在线支付未开启")
    if channel == "mock":
        if not mock_allowed(config):
            raise PaymentError("Mock 支付不可用")
        return MockPaymentProvider(config)
    if channel == "wechat":
        if require_enabled and not config.bool("wechat_pay_enabled"):
            raise PaymentError("微信支付未开启")
        return WechatPayProvider(config)
    if channel == "alipay":
        if require_enabled and not config.bool("alipay_enabled"):
            raise PaymentError("支付宝支付未开启")
        return AlipayProvider(config)
    raise PaymentError(f"未知的支付渠道: {channel}")
