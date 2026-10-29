"""积分流水表 point_transactions

Revision ID: add_point_transactions
Revises: add_tool_access_points
Create Date: 2026-08-31 00:00:00

积分系统完善：记录每笔积分变动（工具消耗 / 管理员设置·充值·扣减），
含变动后余额快照与操作管理员，供用户端「积分明细」与后台「积分流水」查询。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_point_transactions'
down_revision: Union[str, None] = 'add_tool_access_points'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'point_transactions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='用户ID'),
        sa.Column('points', sa.Integer(), nullable=False, comment='变动值(正=获得,负=消耗)'),
        sa.Column('balance_after', sa.Integer(), nullable=False, comment='变动后余额'),
        sa.Column('type', sa.String(length=20), nullable=False, server_default='consume', comment='类型 consume/admin_set/admin_grant/admin_deduct'),
        sa.Column('description', sa.String(length=200), nullable=True, comment='描述'),
        sa.Column('operator_id', sa.BigInteger(), nullable=True, comment='操作管理员ID(后台调整时)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False, comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_point_transactions_user_id', 'point_transactions', ['user_id'])
    op.create_index('ix_point_transactions_type', 'point_transactions', ['type'])
    op.create_index('ix_point_tx_user_created', 'point_transactions', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_point_tx_user_created', table_name='point_transactions')
    op.drop_index('ix_point_transactions_type', table_name='point_transactions')
    op.drop_index('ix_point_transactions_user_id', table_name='point_transactions')
    op.drop_table('point_transactions')
