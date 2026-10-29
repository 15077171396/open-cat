"""users 表新增微信小程序 openid 字段（wechat_openid）

Revision ID: add_wechat_openid
Revises: add_tutorial_seo_fields
Create Date: 2026-10-04 00:00:00

变更内容：
- users.wechat_openid  微信小程序 openid（唯一索引，允许 NULL）

用途：小程序端 wx.login 静默登录 / 账号绑定时写入。Web 端注册的存量用户
该列全部为 NULL（等价于「未绑定微信」，既有账号体系与登录行为完全不变）。
唯一约束保证一个微信身份只对应一个平台账号。

注意：MySQL 唯一索引允许多行 NULL，故未绑定用户的 NULL 值不冲突。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'add_wechat_openid'
down_revision: Union[str, None] = 'add_tutorial_seo_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('wechat_openid', sa.String(length=64), nullable=True, comment='微信小程序 openid(仅小程序登录写入)'),
    )
    op.create_index('ix_users_wechat_openid', 'users', ['wechat_openid'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_wechat_openid', table_name='users')
    op.drop_column('users', 'wechat_openid')
