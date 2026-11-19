"""开放 API：api_tokens / webhooks 两表 + 四个权限码补种

Revision ID: add_open_api
Revises: seed_ip_blacklist_permission
Create Date: 2026-11-20

背景：
    开放 API 第一块拼图（对标 Directus/Strapi 的 API Token + Webhook）：
    - `api_tokens`：个人访问令牌（PAT）。库里只存 SHA-256 摘要 + 展示前缀，
      明文只在签发响应出现一次；作用域白名单 JSON（v1 仅 content:read）。
    - `webhooks`：内容事件出站订阅。URL 仅 https；签名密钥 Fernet 加密落库
      （core/crypto encrypt_secret，enc:v2 版本化）；投递状态回写在端点行上。
    - 权限码 api_token:view / api_token:manage / webhook:view / webhook:manage
      经 rbac_seed_service 补种（0084 同款「以代码目录为唯一事实来源」），
      super_admin 全量、viewer 按目录派生规则自动获得两个 view。

⚠️ 授予面调整只会增加不会回收（rbac_seed_service 只增不减）。
"""

from typing import Sequence, Union

import asyncio

from alembic import op
import sqlalchemy as sa

from app.services.rbac_seed_service import revoke_permission_codes, seed_permission_codes

revision: str = "add_open_api"
down_revision: Union[str, None] = "seed_ip_blacklist_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本次新增的权限码（downgrade 回收这四个）
_NEW_CODES = (
    "api_token:view",
    "api_token:manage",
    "webhook:view",
    "webhook:manage",
)


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False,
                  comment="随机 10 位 ID（防枚举）"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="属主管理员（吊销权限跟随属主）"),
        sa.Column("name", sa.String(length=100), nullable=False, comment="用途备注（展示用）"),
        sa.Column("token_prefix", sa.String(length=16), nullable=False,
                  comment="明文前缀（含 oc_pat_，共 12 位；列表认钥匙用）"),
        sa.Column("token_hash", sa.String(length=64), nullable=False,
                  comment="SHA-256(明文全文) hex —— 认证唯一依据，明文不落库"),
        sa.Column("scopes", sa.JSON(), nullable=True, comment='作用域白名单子集，如 ["content:read"]'),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP"), comment="签发时刻（UTC）"),
        sa.Column("expires_at", sa.DateTime(), nullable=True, comment="过期时刻（UTC，空=永不过期）"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True, comment="最近一次认证成功时刻"),
        sa.Column("revoked_at", sa.DateTime(), nullable=True, comment="吊销时刻（吊销即失效）"),
        sa.Index("uq_api_tokens_token_hash", "token_hash", unique=True),
        sa.Index("ix_api_tokens_owner", "user_id", "created_at"),
        mysql_charset="utf8mb4",
        comment="个人访问令牌（PAT；机器客户端调内容 API 的长期凭据）",
    )
    op.create_table(
        "webhooks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False,
                  comment="随机 10 位 ID（防枚举）"),
        sa.Column("name", sa.String(length=100), nullable=False, comment="用途备注（展示用）"),
        sa.Column("url", sa.String(length=500), nullable=False, comment="回调地址（仅 https）"),
        sa.Column("secret_encrypted", sa.String(length=500), nullable=False,
                  comment="Fernet 加密的签名密钥（enc:v2 版本化）"),
        sa.Column("events", sa.JSON(), nullable=True, comment="订阅事件名子集（WEBHOOK_EVENTS）"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1"),
                  comment="停用后不再投递，但保留配置"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时刻（UTC）"),
        sa.Column("last_status", sa.String(length=16), nullable=True,
                  comment="最近一次投递结果：success/failed/空=从未投递"),
        sa.Column("last_event", sa.String(length=64), nullable=True, comment="最近投递的事件名"),
        sa.Column("last_delivered_at", sa.DateTime(), nullable=True, comment="最近投递时刻（UTC）"),
        sa.Column("last_error", sa.String(length=255), nullable=True, comment="最近失败原因（截断）"),
        sa.Column("failure_streak", sa.Integer(), nullable=False, server_default=sa.text("0"),
                  comment="连续失败次数（成功即清零）"),
        sa.Index("ix_webhooks_active", "active", "created_at"),
        mysql_charset="utf8mb4",
        comment="Webhook 订阅（内容事件出站推送；HMAC-SHA256 签名可验证）",
    )
    asyncio.run(seed_permission_codes(_NEW_CODES))


def downgrade() -> None:
    asyncio.run(revoke_permission_codes(_NEW_CODES))
    op.drop_table("webhooks")
    op.drop_table("api_tokens")
