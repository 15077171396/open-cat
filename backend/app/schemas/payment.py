"""支付接口的响应/请求模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_PASSTHROUGH = ConfigDict(extra="allow")


class PaymentChannelItem(BaseModel):
    model_config = _PASSTHROUGH
    value: str
    label: str


class PaymentCapabilitiesData(BaseModel):
    model_config = _PASSTHROUGH
    enabled: bool = False
    channels: list[PaymentChannelItem] = Field(default_factory=list)


class PaymentProductData(BaseModel):
    model_config = _PASSTHROUGH
    id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    kind: Optional[str] = None
    amount_cents: Optional[int] = None
    amount: Optional[str] = None
    points: Optional[int] = None
    vip_days: Optional[int] = None
    badge: Optional[str] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PaymentProductListData(BaseModel):
    model_config = _PASSTHROUGH
    items: list[PaymentProductData] = Field(default_factory=list)


class PaymentOrderData(BaseModel):
    model_config = _PASSTHROUGH
    order_no: Optional[str] = None
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    product_kind: Optional[str] = None
    amount_cents: Optional[int] = None
    amount: Optional[str] = None
    points: Optional[int] = None
    vip_days: Optional[int] = None
    channel: Optional[str] = None
    provider: Optional[str] = None
    status: Optional[str] = None
    currency: Optional[str] = None
    provider_session_id: Optional[str] = None
    session_id: Optional[str] = None
    paid_at: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    pay_url: Optional[str] = None
    qr_data_url: Optional[str] = None
    qr_content: Optional[str] = None
    user_id: Optional[int] = None
    user_account: Optional[str] = None
    provider_trade_no: Optional[str] = None


class PaymentUserOrdersData(BaseModel):
    model_config = _PASSTHROUGH
    items: list[PaymentOrderData] = Field(default_factory=list)


class PaymentOrderListData(BaseModel):
    model_config = _PASSTHROUGH
    items: list[PaymentOrderData] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class CreatePaymentOrderRequest(BaseModel):
    product_code: str = Field(..., min_length=1, max_length=64)
    # 留空 = 使用默认渠道（DEFAULT_PAYMENT_PROVIDER，默认 stripe）
    channel: Optional[str] = Field(None, pattern="^(mock|wechat|alipay|stripe)$")


class MembershipPlanData(BaseModel):
    model_config = _PASSTHROUGH
    code: str
    name: str
    description: str = ""
    amount_cents: int = 0
    amount: str = "0.00"
    vip_days: int = 0
    badge: str = ""
    currency: str = "CNY"


class MembershipPlansData(BaseModel):
    model_config = _PASSTHROUGH
    items: list[MembershipPlanData] = Field(default_factory=list)
    currency: str = "CNY"
    default_channel: Optional[str] = None
    channels: list[PaymentChannelItem] = Field(default_factory=list)


class MembershipStateData(BaseModel):
    model_config = _PASSTHROUGH
    user_id: Optional[int] = None
    role: Optional[str] = None
    is_vip: bool = False
    vip_expire_time: Optional[str] = None


class MembershipCheckoutData(BaseModel):
    model_config = _PASSTHROUGH
    order: PaymentOrderData
    membership: MembershipStateData


class CreateMembershipCheckoutRequest(BaseModel):
    product_code: str = Field(..., min_length=1, max_length=64)
    channel: Optional[str] = Field(None, pattern="^(mock|wechat|alipay|stripe)$")


class PaymentProductWriteRequest(BaseModel):
    code: Optional[str] = Field(None, max_length=64)
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=300)
    kind: Optional[str] = Field(None, pattern="^(points|vip)$")
    amount_cents: Optional[int] = Field(None, ge=1, le=100_000_000)
    points: Optional[int] = Field(None, ge=0, le=10_000_000)
    vip_days: Optional[int] = Field(None, ge=0, le=3650)
    badge: Optional[str] = Field(None, max_length=30)
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0, le=1_000_000)
