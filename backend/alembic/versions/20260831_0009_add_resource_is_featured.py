"""resources.is_featured 精选推荐字段

Revision ID: add_resource_is_featured
Revises: add_tutorial_is_free
Create Date: 2026-08-31 00:00:00

资源精选推荐：
- is_featured=True  后台开启推荐的资源，前台资源页「精选资源推荐」列表优先展示
- is_featured=False 默认，不影响原有排序
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_resource_is_featured'
down_revision: Union[str, None] = 'add_tutorial_is_free'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'resources',
        sa.Column('is_featured', sa.Boolean(), nullable=False, server_default=sa.text('0'), comment='是否精选推荐'),
    )


def downgrade() -> None:
    op.drop_column('resources', 'is_featured')
