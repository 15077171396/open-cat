"""用户界面偏好与锁屏口令表：user_ui_preferences

Revision ID: add_user_ui_preferences
Revises: seed_chat_permission
Create Date: 2026-11-04

每用户单行（user_id 主键），preferences 存白名单校验后的 JSON；
锁屏口令只存 bcrypt 摘要与更新时间，明文永不落库。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_user_ui_preferences"
down_revision: Union[str, None] = "seed_chat_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_ui_preferences",
        sa.Column(
            "user_id", sa.BigInteger(), autoincrement=False, nullable=False,
            comment="用户 ID",
        ),
        sa.Column(
            "preferences", sa.JSON(), nullable=True,
            comment="界面偏好 JSON（仅白名单字段）",
        ),
        sa.Column(
            "lock_password_hash", sa.String(length=255), nullable=True,
            comment="锁屏口令 bcrypt 摘要（绝不返回/记日志）",
        ),
        sa.Column(
            "lock_password_updated_at", sa.DateTime(), nullable=True,
            comment="锁屏口令最近更新时间",
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_ui_preferences")
