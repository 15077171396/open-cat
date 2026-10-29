"""新增系统设置表

Revision ID: add_system_settings
Revises: add_res_cover_tags_pinned
Create Date: 2026-09-03 00:00:00

系统设置功能：站点 Logo/平台名称、各模块首页主视觉图、
教程/资源默认封面、用户默认头像、社区与星球认证二维码，
以 key-value 存储，新增设置项只需加代码定义，无需再改表。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'add_system_settings'
down_revision: Union[str, None] = 'add_res_cover_tags_pinned'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'system_settings',
        sa.Column('setting_key', sa.String(100), primary_key=True, comment='设置键'),
        sa.Column('value', sa.Text(), nullable=True, comment='设置值（NULL=使用默认值）'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='更新时间'),
    )


def downgrade() -> None:
    op.drop_table('system_settings')
