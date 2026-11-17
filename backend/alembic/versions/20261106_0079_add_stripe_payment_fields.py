"""支付订单补充 Stripe 字段：currency / provider_session_id

Revision ID: add_stripe_payment_fields
Revises: add_user_ui_preferences
Create Date: 2026-11-06

Stripe Checkout 需要记录会话号以便回跳后服务端查单；币种随渠道记录，
避免只存「分」而丢失结算币种。channel 字段本身即 provider，无需重复新增。

⚠️ 迁移 head 变化：tests/test_admin_audit.py 的硬编码 expected head 已同步为
add_stripe_payment_fields。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_stripe_payment_fields"
down_revision: Union[str, None] = "add_user_ui_preferences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_orders",
        sa.Column(
            "currency", sa.String(length=8), server_default="CNY", nullable=False,
            comment="币种（ISO 4217）",
        ),
    )
    op.add_column(
        "payment_orders",
        sa.Column(
            "provider_session_id", sa.String(length=255), nullable=True,
            comment="第三方会话号（Stripe Checkout Session）",
        ),
    )
    op.create_index(
        "ix_payment_orders_provider_session_id",
        "payment_orders",
        ["provider_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_payment_orders_provider_session_id", table_name="payment_orders")
    op.drop_column("payment_orders", "provider_session_id")
    op.drop_column("payment_orders", "currency")
