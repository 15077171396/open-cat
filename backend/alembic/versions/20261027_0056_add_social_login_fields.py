"""账号登录扩展：手机号 / 微信 UnionID / 网站应用 openid

Revision ID: add_social_login_fields
Revises: add_ai_models
Create Date: 2026-10-27

为什么需要：
    - 手机号验证码登录需要独立 phone 列：邮箱账号也要能绑定手机号；
      历史「account 就是手机号」的老数据回填到 phone，避免被当成未注册。
    - PC 扫码登录的 openid 与小程序 openid 来自不同 AppID，必须分开存；
      同时新增 unionid 作为跨端合并键（优先 unionid，其次各自 openid）。

安全/约束：
    - phone / unionid / web openid 都唯一且可空；MySQL 唯一索引允许多个 NULL。
    - 回填只处理符合大陆手机号格式的 account，不修改 account 本身。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_social_login_fields"
down_revision: Union[str, None] = "add_ai_models"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("phone", sa.String(length=20), nullable=True, comment="手机号(唯一, 可与 account 不同)"),
    )
    op.add_column(
        "users",
        sa.Column("phone_verified_at", sa.DateTime(), nullable=True, comment="手机号验证通过时间"),
    )
    op.add_column(
        "users",
        sa.Column("wechat_unionid", sa.String(length=64), nullable=True, comment="微信 UnionID(跨应用账号合并)"),
    )
    op.add_column(
        "users",
        sa.Column("wechat_web_openid", sa.String(length=64), nullable=True, comment="微信开放平台网站应用 openid"),
    )

    op.create_index("ix_users_phone", "users", ["phone"], unique=True)
    op.create_index("ix_users_wechat_unionid", "users", ["wechat_unionid"], unique=True)
    op.create_index("ix_users_wechat_web_openid", "users", ["wechat_web_openid"], unique=True)

    # 历史数据回填：account 形如大陆手机号且尚未绑定 phone 的用户
    op.execute(
        """
        UPDATE users
        SET phone = account
        WHERE phone IS NULL
          AND account REGEXP '^1[3-9][0-9]{9}$'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_users_wechat_web_openid", table_name="users")
    op.drop_index("ix_users_wechat_unionid", table_name="users")
    op.drop_index("ix_users_phone", table_name="users")
    op.drop_column("users", "wechat_web_openid")
    op.drop_column("users", "wechat_unionid")
    op.drop_column("users", "phone_verified_at")
    op.drop_column("users", "phone")
