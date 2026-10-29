"""支付商品与订单：payment_products / payment_orders + 权限补种

Revision ID: add_payments
Revises: add_social_login_fields
Create Date: 2026-10-27

设计说明：
    - 商品与订单分离，订单保存商品快照（改价不影响历史订单）；
    - 金额统一存「分」整数，避免浮点误差；
    - 订单状态机 pending → paid → closed/refunded，回调用条件更新保证幂等；
    - 初始套餐只是占位，运营可在「充值套餐」页改价/上下架。

⚠️ 迁移 head 变化：tests/test_admin_audit.py 的硬编码 expected head 已同步为 add_payments。
"""

import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.rbac_catalog import PERMISSION_CATALOG, SYSTEM_ROLES

revision: str = "add_payments"
down_revision: Union[str, None] = "add_social_login_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_PERMISSION_CODES = (
    "payment_product:view",
    "payment_product:create",
    "payment_product:edit",
    "payment_product:delete",
    "payment_order:view",
)

_SEED_PRODUCTS = (
    # code, name, description, kind, amount_cents, points, vip_days, badge, sort
    ("points_100", "100 积分", "可用于购买教程或运行 AI 工具", "points", 1000, 100, 0, None, 10),
    ("points_500", "500 积分", "多买多省，适合高频使用", "points", 4500, 500, 0, "推荐", 20),
    ("vip_30", "VIP 30 天", "会员月卡，到期自动失效", "vip", 2900, 0, 30, None, 30),
    ("vip_365", "VIP 365 天", "会员年卡，长期学习更划算", "vip", 19900, 0, 365, "最划算", 40),
)


def _gen_id() -> int:
    return secrets.randbelow(9_000_000_000) + 1_000_000_000


def _seed_permissions(conn) -> None:
    existing = {row.code for row in conn.execute(sa.text("SELECT code FROM permissions"))}
    for p in PERMISSION_CATALOG:
        if p.code in existing:
            conn.execute(
                sa.text(
                    "UPDATE permissions SET name=:name, type=:type, module=:module, "
                    "module_name=:module_name, sort=:sort WHERE code=:code"
                ).bindparams(
                    name=p.name,
                    type=p.type,
                    module=p.module,
                    module_name=p.module_name,
                    sort=p.sort,
                    code=p.code,
                )
            )
        else:
            conn.execute(
                sa.text(
                    "INSERT INTO permissions (id, code, name, type, module, module_name, sort) "
                    "VALUES (:id, :code, :name, :type, :module, :module_name, :sort)"
                ).bindparams(
                    id=_gen_id(),
                    code=p.code,
                    name=p.name,
                    type=p.type,
                    module=p.module,
                    module_name=p.module_name,
                    sort=p.sort,
                )
            )

    code_to_perm_id = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM permissions"))
    }
    code_to_role_id = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM roles"))
    }
    for role in SYSTEM_ROLES:
        role_id = code_to_role_id.get(role["code"])
        if role_id is None:
            continue
        granted = {
            row.code
            for row in conn.execute(
                sa.text(
                    "SELECT p.code FROM role_permissions rp "
                    "JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = :rid"
                ).bindparams(rid=role_id)
            )
        }
        for code in role["permissions"]:
            if code in granted:
                continue
            perm_id = code_to_perm_id.get(code)
            if perm_id is None:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"
                ).bindparams(rid=role_id, pid=perm_id)
            )


def upgrade() -> None:
    op.create_table(
        "payment_products",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False, comment="主键"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="套餐标识"),
        sa.Column("name", sa.String(length=100), nullable=False, comment="展示名称"),
        sa.Column("description", sa.String(length=300), nullable=True, comment="副标题/说明"),
        sa.Column("kind", sa.String(length=20), nullable=False, comment="权益类型 points/vip"),
        sa.Column("amount_cents", sa.Integer(), nullable=False, comment="售价（分，CNY）"),
        sa.Column("points", sa.Integer(), server_default="0", nullable=False, comment="到账积分"),
        sa.Column("vip_days", sa.Integer(), server_default="0", nullable=False, comment="VIP 天数"),
        sa.Column("badge", sa.String(length=30), nullable=True, comment="角标"),
        sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False, comment="是否上架"),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_products_code", "payment_products", ["code"], unique=True)
    op.create_index("ix_payment_products_kind", "payment_products", ["kind"], unique=False)
    op.create_index("ix_payment_products_is_enabled", "payment_products", ["is_enabled"], unique=False)
    op.create_index("ix_payment_products_updated_at", "payment_products", ["updated_at"], unique=False)

    op.create_table(
        "payment_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False, comment="主键"),
        sa.Column("order_no", sa.String(length=40), nullable=False, comment="商户订单号"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="用户ID"),
        sa.Column("product_code", sa.String(length=64), nullable=False, comment="商品 code 快照"),
        sa.Column("product_name", sa.String(length=100), nullable=False, comment="商品名称快照"),
        sa.Column("product_kind", sa.String(length=20), nullable=False, comment="points/vip"),
        sa.Column("amount_cents", sa.Integer(), nullable=False, comment="应付金额（分）"),
        sa.Column("points", sa.Integer(), server_default="0", nullable=False, comment="积分快照"),
        sa.Column("vip_days", sa.Integer(), server_default="0", nullable=False, comment="VIP 天数快照"),
        sa.Column("channel", sa.String(length=20), nullable=False, comment="mock/wechat/alipay"),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False, comment="pending/paid/closed/refunded/failed"),
        sa.Column("provider_trade_no", sa.String(length=64), nullable=True, comment="第三方交易号"),
        sa.Column("provider_payload", sa.JSON(), nullable=True, comment="下单/回调摘要（不含密钥）"),
        sa.Column("paid_at", sa.DateTime(), nullable=True, comment="支付时间"),
        sa.Column("expires_at", sa.DateTime(), nullable=True, comment="订单过期时间"),
        sa.Column("client_ip", sa.String(length=45), nullable=True, comment="下单 IP"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_orders_order_no", "payment_orders", ["order_no"], unique=True)
    op.create_index("ix_payment_orders_user_id", "payment_orders", ["user_id"], unique=False)
    op.create_index("ix_payment_orders_channel", "payment_orders", ["channel"], unique=False)
    op.create_index("ix_payment_orders_status", "payment_orders", ["status"], unique=False)
    op.create_index("ix_payment_orders_provider_trade_no", "payment_orders", ["provider_trade_no"], unique=True)
    op.create_index("ix_payment_orders_updated_at", "payment_orders", ["updated_at"], unique=False)
    op.create_index("ix_payment_orders_user_created", "payment_orders", ["user_id", "created_at"], unique=False)
    op.create_index("ix_payment_orders_status_created", "payment_orders", ["status", "created_at"], unique=False)

    conn = op.get_bind()
    for code, name, description, kind, amount, points, vip_days, badge, sort in _SEED_PRODUCTS:
        conn.execute(
            sa.text(
                "INSERT INTO payment_products "
                "(id, code, name, description, kind, amount_cents, points, vip_days, badge, "
                "is_enabled, sort_order) "
                "VALUES (:id, :code, :name, :description, :kind, :amount, :points, :vip_days, "
                ":badge, 1, :sort)"
            ).bindparams(
                id=_gen_id(),
                code=code,
                name=name,
                description=description,
                kind=kind,
                amount=amount,
                points=points,
                vip_days=vip_days,
                badge=badge,
                sort=sort,
            )
        )

    _seed_permissions(conn)


def downgrade() -> None:
    conn = op.get_bind()
    for code in _NEW_PERMISSION_CODES:
        perm = conn.execute(
            sa.text("SELECT id FROM permissions WHERE code = :code").bindparams(code=code)
        ).first()
        if perm is None:
            continue
        conn.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_id = :pid").bindparams(pid=perm.id)
        )
        conn.execute(
            sa.text("DELETE FROM permissions WHERE id = :pid").bindparams(pid=perm.id)
        )

    for index in (
        "ix_payment_orders_status_created",
        "ix_payment_orders_user_created",
        "ix_payment_orders_updated_at",
        "ix_payment_orders_provider_trade_no",
        "ix_payment_orders_status",
        "ix_payment_orders_channel",
        "ix_payment_orders_user_id",
        "ix_payment_orders_order_no",
    ):
        op.drop_index(index, table_name="payment_orders")
    op.drop_table("payment_orders")

    for index in (
        "ix_payment_products_updated_at",
        "ix_payment_products_is_enabled",
        "ix_payment_products_kind",
        "ix_payment_products_code",
    ):
        op.drop_index(index, table_name="payment_products")
    op.drop_table("payment_products")
