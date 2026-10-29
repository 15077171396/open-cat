"""邀请返利：users 表新增邀请码/邀请人字段 + invite_relations 邀请关系表

Revision ID: add_invite_referral
Revises: add_redemption_codes
Create Date: 2026-09-07 00:00:00

邀请返利功能：
- users.invite_code   用户专属邀请码（访问邀请页时惰性生成，存量用户为 NULL）
- users.referrer_id   注册时通过邀请码绑定的邀请人 ID
- invite_relations    邀请关系明细（invitee_id 唯一约束防止同一用户重复绑定/重复发奖）

invite_code 唯一索引建立在可空列上（MySQL 允许多个 NULL），存量用户互不冲突。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_invite_referral'
down_revision: Union[str, None] = 'add_redemption_codes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('invite_code', sa.String(length=16), nullable=True, comment='专属邀请码(访问邀请页时惰性生成)'),
    )
    op.add_column(
        'users',
        sa.Column('referrer_id', sa.BigInteger(), nullable=True, comment='邀请人ID(注册时通过邀请码绑定)'),
    )
    op.create_index('ix_users_invite_code', 'users', ['invite_code'], unique=True)
    op.create_index('ix_users_referrer_id', 'users', ['referrer_id'])

    op.create_table(
        'invite_relations',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('inviter_id', sa.BigInteger(), nullable=False, comment='邀请人ID'),
        sa.Column('invitee_id', sa.BigInteger(), nullable=False, comment='被邀请人ID(一人仅可被邀请一次)'),
        sa.Column('reward_points', sa.Integer(), nullable=False, server_default='0', comment='邀请人获得的积分'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False, comment='绑定时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invitee_id', name='uq_invite_relations_invitee'),
    )
    op.create_index('ix_invite_relations_inviter_id', 'invite_relations', ['inviter_id'])
    op.create_index('ix_invite_relations_invitee_id', 'invite_relations', ['invitee_id'])


def downgrade() -> None:
    op.drop_index('ix_invite_relations_invitee_id', table_name='invite_relations')
    op.drop_index('ix_invite_relations_inviter_id', table_name='invite_relations')
    op.drop_table('invite_relations')
    op.drop_index('ix_users_referrer_id', table_name='users')
    op.drop_index('ix_users_invite_code', table_name='users')
    op.drop_column('users', 'referrer_id')
    op.drop_column('users', 'invite_code')
