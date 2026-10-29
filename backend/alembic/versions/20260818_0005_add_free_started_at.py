"""users 新增 free_started_at 免费时长起点字段

Revision ID: add_free_started_at
Revises: add_composite_indexes
Create Date: 2026-08-18

免费 3 小时规则由「使用时累计」改为「注册起自然时间倒计时」：
- 新用户注册时写入 free_started_at = now
- 存量用户回填 free_started_at = created_at（按注册时间起算，
  注册超过 3 小时的立即耗尽）
- free_used_seconds 保留，仅作实际学习时长统计
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'add_free_started_at'
down_revision: Union[str, None] = 'composite_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'free_started_at',
            sa.DateTime(),
            nullable=True,
            comment='免费时长起点(注册时间)',
        ),
    )
    # 回填存量用户：按注册时间起算
    op.execute('UPDATE users SET free_started_at = created_at WHERE free_started_at IS NULL')


def downgrade() -> None:
    op.drop_column('users', 'free_started_at')
