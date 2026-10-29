"""教程权限模型改造：免费/会员/积分购买 + 置顶/推荐

Revision ID: add_tutorial_access_purchase
Revises: add_system_settings
Create Date: 2026-09-06 00:00:00

- tutorials 表：is_free（游客可读/需登录+计时）改为 access_mode（free/vip，
  均需登录），新增 points_price（会员教程积分购买价）、is_pinned（置顶）、
  is_recommended（推荐）
- 新增 tutorial_purchases 表：用户积分购买记录，购买后永久可读
- 数据迁移：is_free=1 → free；is_free=0 → vip（原计时付费教程归为会员教程）
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'add_tutorial_access_purchase'
down_revision: Union[str, None] = 'add_system_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. tutorials 新增权限/置顶/推荐字段（server_default 保证存量行回填默认值）
    op.add_column('tutorials', sa.Column('access_mode', sa.String(length=20), nullable=False, server_default='free', comment='访问模式: free/vip'))
    op.add_column('tutorials', sa.Column('points_price', sa.Integer(), nullable=False, server_default='0', comment='积分购买价(会员教程>0时支持积分购买)'))
    op.add_column('tutorials', sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default=sa.text('0'), comment='是否置顶'))
    op.add_column('tutorials', sa.Column('is_recommended', sa.Boolean(), nullable=False, server_default=sa.text('0'), comment='是否推荐'))

    # 2. 存量数据迁移：原免费阅读教程 → free；原需认证（计时）教程 → vip
    op.execute("UPDATE tutorials SET access_mode = CASE WHEN is_free = 1 THEN 'free' ELSE 'vip' END")

    # 3. 删除旧权限字段（语义已被 access_mode 取代）
    op.drop_column('tutorials', 'is_free')

    # 4. 教程积分购买记录表
    op.create_table(
        'tutorial_purchases',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='用户ID'),
        sa.Column('tutorial_id', sa.BigInteger(), nullable=False, comment='教程ID'),
        sa.Column('points_paid', sa.Integer(), nullable=False, comment='购买时消耗的积分'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False, comment='购买时间'),
        sa.UniqueConstraint('user_id', 'tutorial_id', name='uq_tutorial_purchase_user_tutorial'),
    )
    op.create_index('ix_tutorial_purchases_user_id', 'tutorial_purchases', ['user_id'])
    op.create_index('ix_tutorial_purchase_tutorial', 'tutorial_purchases', ['tutorial_id'])


def downgrade() -> None:
    op.drop_index('ix_tutorial_purchase_tutorial', table_name='tutorial_purchases')
    op.drop_index('ix_tutorial_purchases_user_id', table_name='tutorial_purchases')
    op.drop_table('tutorial_purchases')

    # 回滚需恢复 is_free：access_mode='free' → 1，其余 → 0
    op.add_column('tutorials', sa.Column('is_free', sa.Boolean(), nullable=False, server_default=sa.text('0'), comment='是否免费阅读'))
    op.execute("UPDATE tutorials SET is_free = CASE WHEN access_mode = 'free' THEN 1 ELSE 0 END")
    op.drop_column('tutorials', 'is_recommended')
    op.drop_column('tutorials', 'is_pinned')
    op.drop_column('tutorials', 'points_price')
    op.drop_column('tutorials', 'access_mode')
