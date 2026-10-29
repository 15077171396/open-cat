"""add soft delete and indexes

Revision ID: dace17c39324
Revises: 5f29a20f44e1
Create Date: 2026-07-30 22:15:07.862854

ARCH-012 软删除: users.deleted_at (注销标记, NULL=正常)
PERF-009 索引: tutorials.is_published / tutorials.status / downloads.created_at
              tools.is_published / messages.is_read (补齐高频过滤字段索引)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'dace17c39324'
down_revision: Union[str, None] = '5f29a20f44e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ========== ARCH-012 软删除 ==========
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(), nullable=True, comment='软删除时间(注销标记, NULL=正常)'))
    op.create_index(op.f('ix_users_deleted_at'), 'users', ['deleted_at'], unique=False)

    # ========== PERF-009 高频过滤字段索引 ==========
    op.create_index(op.f('ix_tutorials_is_published'), 'tutorials', ['is_published'], unique=False)
    op.create_index(op.f('ix_tutorials_status'), 'tutorials', ['status'], unique=False)
    op.create_index(op.f('ix_downloads_created_at'), 'downloads', ['created_at'], unique=False)
    op.create_index(op.f('ix_tools_is_published'), 'tools', ['is_published'], unique=False)
    # messages.is_read 模型已声明 index=True 但历史迁移漏建, 此处补齐
    op.create_index(op.f('ix_messages_is_read'), 'messages', ['is_read'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_messages_is_read'), table_name='messages')
    op.drop_index(op.f('ix_tools_is_published'), table_name='tools')
    op.drop_index(op.f('ix_downloads_created_at'), table_name='downloads')
    op.drop_index(op.f('ix_tutorials_status'), table_name='tutorials')
    op.drop_index(op.f('ix_tutorials_is_published'), table_name='tutorials')
    op.drop_index(op.f('ix_users_deleted_at'), table_name='users')
    op.drop_column('users', 'deleted_at')
