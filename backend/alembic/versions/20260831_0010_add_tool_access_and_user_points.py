"""tools 权限模式 + users 积分字段

Revision ID: add_tool_access_points
Revises: add_resource_is_featured
Create Date: 2026-08-31 00:00:00

在线工具权限体系：
- tools.access_mode  使用权限：free=免费 / auth=需认证(默认，原行为) / points=需积分
- tools.points_cost  积分模式下每次运行消耗的积分数
- users.points       积分余额（管理后台可调整，运行积分工具时扣减）

存量数据兼容：access_mode 默认 'auth' 与原免费时长门槛行为一致；
points_cost 默认 1；users.points 默认 0。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_tool_access_points'
down_revision: Union[str, None] = 'add_resource_is_featured'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tools',
        sa.Column('access_mode', sa.String(length=20), nullable=False, server_default='auth', comment='使用权限 free/auth/points'),
    )
    op.add_column(
        'tools',
        sa.Column('points_cost', sa.Integer(), nullable=False, server_default='1', comment='积分模式下每次运行消耗的积分'),
    )
    op.create_index('ix_tools_access_mode', 'tools', ['access_mode'])
    op.add_column(
        'users',
        sa.Column('points', sa.Integer(), nullable=False, server_default='0', comment='积分余额(运行积分工具消耗)'),
    )


def downgrade() -> None:
    op.drop_column('users', 'points')
    op.drop_index('ix_tools_access_mode', table_name='tools')
    op.drop_column('tools', 'points_cost')
    op.drop_column('tools', 'access_mode')
