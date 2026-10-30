"""add role data_scope

给 RBAC 角色加「数据范围」：此前权限只到按钮级（能不能点），管不到「能看见谁的数据」。
多人协作时这是刚需 —— 运营只能看本部门的用户，客服只能看自己的操作记录。

默认 ``all``：存量角色保持改造前的可见范围，升级不会突然让人看不到数据。

Revision ID: 8c014540ffa7
Revises: drop_legacy_content_tables
Create Date: 2026-10-30 16:03:03.685792

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8c014540ffa7'
down_revision: Union[str, None] = 'drop_legacy_content_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "data_scope",
            sa.String(length=20),
            nullable=False,
            server_default="all",
            comment="数据范围：all=全部 / dept=本部门 / self=仅本人（见 core/data_scope.py）",
        ),
    )


def downgrade() -> None:
    op.drop_column("roles", "data_scope")
