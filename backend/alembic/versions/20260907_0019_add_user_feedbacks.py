"""用户问题反馈表

Revision ID: add_user_feedbacks
Revises: setting_value_mediumtext
Create Date: 2026-09-07

前台右侧工具栏「问题反馈」弹框提交，管理后台审阅处理。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_user_feedbacks'
down_revision: Union[str, None] = 'setting_value_mediumtext'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'feedbacks',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='提交用户ID'),
        sa.Column('content', sa.Text(), nullable=False, comment='反馈内容'),
        sa.Column('contact', sa.String(length=100), nullable=True, comment='联系方式(可选)'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending',
                  comment='状态: pending/processing/resolved/closed'),
        sa.Column('admin_note', sa.Text(), nullable=True, comment='管理员备注(内部)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_feedbacks_user_id', 'feedbacks', ['user_id'])
    op.create_index('ix_feedbacks_status', 'feedbacks', ['status'])


def downgrade() -> None:
    op.drop_index('ix_feedbacks_status', table_name='feedbacks')
    op.drop_index('ix_feedbacks_user_id', table_name='feedbacks')
    op.drop_table('feedbacks')
