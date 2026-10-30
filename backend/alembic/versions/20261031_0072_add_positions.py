"""新增岗位目录表 positions

Revision ID: add_positions
Revises: add_dictionaries
Create Date: 2026-10-31

岗位先作为组织架构目录提供 CRUD；用户与岗位的绑定在后续迁移中接入。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_positions"
down_revision: Union[str, None] = "add_dictionaries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False, comment="岗位编码"),
        sa.Column("name", sa.String(length=50), nullable=False, comment="岗位名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="岗位职责说明"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="排序"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="是否启用"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_positions_code"),
        sa.UniqueConstraint("name", name="uq_positions_name"),
    )
    op.create_index("ix_positions_code", "positions", ["code"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_positions_code", table_name="positions")
    op.drop_table("positions")
