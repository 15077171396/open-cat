"""tutorials.is_free 阅读权限字段

Revision ID: add_tutorial_is_free
Revises: add_tutorial_nodes
Create Date: 2026-08-31 00:00:00

教程阅读权限控制：
- is_free=True  免费阅读：游客可直接读正文，不登录、不计时
- is_free=False 需认证阅读（默认）：登录 + 注册起 3 小时免费时长，耗尽需知识星球认证
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_tutorial_is_free'
down_revision: Union[str, None] = 'add_tutorial_nodes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tutorials',
        sa.Column('is_free', sa.Boolean(), nullable=False, server_default=sa.text('0'), comment='是否免费阅读'),
    )


def downgrade() -> None:
    op.drop_column('tutorials', 'is_free')
