"""资源列表默认排序复合索引：is_pinned DESC, is_featured DESC, updated_at DESC

Revision ID: add_resource_sort_index
Revises: add_user_feedbacks
Create Date: 2026-09-08 00:00:00

资源列表默认排序含三列（置顶 > 精选 > 更新时间），此前无索引支撑产生 filesort。
对齐教程表 (is_published, published_at) 复合索引先例（composite_indexes 迁移）。
另为 download_count/updated_at 热榜排序补 updated_at 单列索引（热榜与最新列表共用）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_resource_sort_index'
down_revision: Union[str, None] = 'add_user_feedbacks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_resources_pin_featured_updated',
        'resources',
        ['is_pinned', 'is_featured', 'updated_at'],
    )
    op.create_index(
        'ix_resources_updated_at',
        'resources',
        ['updated_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_resources_updated_at', table_name='resources')
    op.drop_index('ix_resources_pin_featured_updated', table_name='resources')
