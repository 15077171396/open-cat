"""资源表新增封面图/标签/置顶字段

Revision ID: add_res_cover_tags_pinned
Revises: remove_autoincrement
Create Date: 2026-09-03 00:00:00

资源卡片改版：顶部封面图 + 右上角标签角标（置顶/推荐/最新），
详情页 Hero 以封面图为背景。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'add_res_cover_tags_pinned'
down_revision: Union[str, None] = 'remove_autoincrement'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('resources', sa.Column('cover_image', sa.String(500), nullable=True, comment='封面图URL'))
    op.add_column('resources', sa.Column('tags', sa.JSON(), nullable=True, comment='资源标签'))
    op.add_column('resources', sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default=sa.text('0'), comment='是否置顶'))


def downgrade() -> None:
    op.drop_column('resources', 'is_pinned')
    op.drop_column('resources', 'tags')
    op.drop_column('resources', 'cover_image')
