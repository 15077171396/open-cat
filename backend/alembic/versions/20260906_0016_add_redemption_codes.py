"""积分兑换码表 redemption_codes / redemption_records

Revision ID: add_redemption_codes
Revises: add_tutorial_access_purchase
Create Date: 2026-09-06 00:00:00

积分兑换码：管理员创建（单人码/多人限量码 + 积分 + 过期时间），
用户在个人中心-积分页兑换。多人码并发核销由
「原子 UPDATE ... WHERE used_count < max_uses」保证不超发，
同一用户重复兑换由 redemption_records 的 (code_id, user_id)
唯一约束兜底。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_redemption_codes'
down_revision: Union[str, None] = 'add_tutorial_access_purchase'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'redemption_codes',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False, comment='兑换码'),
        sa.Column('points', sa.Integer(), nullable=False, comment='每份兑换积分'),
        sa.Column('max_uses', sa.Integer(), nullable=False, comment='最大兑换人数(单人码=1)'),
        sa.Column('used_count', sa.Integer(), nullable=False, server_default='0', comment='已兑换人数'),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='active', comment='active/disabled'),
        sa.Column('expires_at', sa.DateTime(), nullable=True, comment='过期时间(空=永不过期)'),
        sa.Column('note', sa.String(length=200), nullable=True, comment='备注'),
        sa.Column('created_by', sa.BigInteger(), nullable=True, comment='创建管理员ID'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False, comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_redemption_codes_code'),
    )
    op.create_index('ix_redemption_codes_status_created', 'redemption_codes', ['status', 'created_at'])

    op.create_table(
        'redemption_records',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('code_id', sa.BigInteger(), nullable=False, comment='兑换码ID'),
        sa.Column('code', sa.String(length=32), nullable=False, comment='兑换码快照(便于追溯)'),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='用户ID'),
        sa.Column('points', sa.Integer(), nullable=False, comment='获得积分'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False, comment='兑换时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code_id', 'user_id', name='uq_redemption_code_user'),
    )
    op.create_index('ix_redemption_records_code_id', 'redemption_records', ['code_id'])
    op.create_index('ix_redemption_records_user_id', 'redemption_records', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_redemption_records_user_id', table_name='redemption_records')
    op.drop_index('ix_redemption_records_code_id', table_name='redemption_records')
    op.drop_table('redemption_records')
    op.drop_index('ix_redemption_codes_status_created', table_name='redemption_codes')
    op.drop_table('redemption_codes')
