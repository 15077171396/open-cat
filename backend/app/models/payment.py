"""支付商品与订单模型。

商品与订单分离：订单保存商品快照（名称/金额/权益），后台改价不会影响已下单订单。
金额统一用「分」整数，避免浮点误差；展示时再转元。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class PaymentProduct(Base):
    """充值套餐（积分 / VIP）"""

    __tablename__ = "payment_products"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="套餐标识")
    name: Mapped[str] = mapped_column(String(100), comment="展示名称")
    description: Mapped[str | None] = mapped_column(String(300), nullable=True, comment="副标题/说明")
    kind: Mapped[str] = mapped_column(String(20), index=True, comment="权益类型 points/vip")
    amount_cents: Mapped[int] = mapped_column(Integer, comment="售价（分，CNY）")
    points: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="到账积分")
    vip_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="VIP 天数")
    badge: Mapped[str | None] = mapped_column(String(30), nullable=True, comment="角标，如「推荐」")
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", index=True, comment="是否上架"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), index=True, comment="更新时间"
    )


class PaymentOrder(Base):
    """支付订单（pending → paid → closed/refunded）"""

    __tablename__ = "payment_orders"
    __table_args__ = (
        Index("ix_payment_orders_user_created", "user_id", "created_at"),
        Index("ix_payment_orders_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    order_no: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, comment="商户订单号"
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="用户ID")
    product_code: Mapped[str] = mapped_column(String(64), comment="商品 code 快照")
    product_name: Mapped[str] = mapped_column(String(100), comment="商品名称快照")
    product_kind: Mapped[str] = mapped_column(String(20), comment="points/vip")
    amount_cents: Mapped[int] = mapped_column(Integer, comment="应付金额（分）")
    points: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="积分快照")
    vip_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="VIP 天数快照")
    channel: Mapped[str] = mapped_column(String(20), index=True, comment="mock/wechat/alipay")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True,
        comment="pending/paid/closed/refunded/failed",
    )
    provider_trade_no: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True, comment="第三方交易号"
    )
    provider_payload: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="下单/回调摘要（不含密钥）"
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="支付时间")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="订单过期时间")
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True, comment="下单 IP")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), index=True, comment="更新时间"
    )
