"""分类分组：新增 category_groups，categories 增加 group_id

Revision ID: add_category_groups
Revises: add_material_fields
Create Date: 2026-10-30

为什么分组也带 module：分组只在同一内容类型内部有意义（见 models/category.py）。
`categories.group_id` 允许 NULL —— 分组功能上线前的存量分类全部落在「未分组」，
不迁移数据、不改内容表，回滚也只是丢分组关系。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_category_groups"
down_revision: Union[str, None] = "add_material_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "category_groups",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("module", sa.String(length=20), nullable=False, comment="内容类型键"),
        sa.Column("name", sa.String(length=50), nullable=False, comment="分组名称"),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0", comment="排序权重（小的在前）"
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("module", "name", name="uq_category_group_module_name"),
    )
    op.create_index("ix_category_groups_module", "category_groups", ["module"])

    op.add_column(
        "categories",
        sa.Column("group_id", sa.BigInteger(), nullable=True, comment="所属分组 id；NULL=未分组"),
    )
    op.create_index("ix_categories_group_id", "categories", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_categories_group_id", table_name="categories")
    op.drop_column("categories", "group_id")
    op.drop_index("ix_category_groups_module", table_name="category_groups")
    op.drop_table("category_groups")
