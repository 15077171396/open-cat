"""高频查询复合索引

Revision ID: composite_indexes
Revises: add_tutorial_intro
Create Date: 2026-08-18

为高频查询路径补充复合索引，避免回表 + filesort：
- messages (user_id, is_read): 未读数统计与消息列表过滤
- downloads (user_id, created_at): 下载历史按时间倒序分页
- tutorials (is_published, published_at): 公开教程列表默认排序
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'composite_indexes'
down_revision: Union[str, None] = 'add_tutorial_intro'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_messages_user_read', 'messages', ['user_id', 'is_read']
    )
    op.create_index(
        'ix_downloads_user_created', 'downloads', ['user_id', 'created_at']
    )
    op.create_index(
        'ix_tutorials_published_at', 'tutorials', ['is_published', 'published_at']
    )


def downgrade() -> None:
    op.drop_index('ix_tutorials_published_at', table_name='tutorials')
    op.drop_index('ix_downloads_user_created', table_name='downloads')
    op.drop_index('ix_messages_user_read', table_name='messages')
