"""内容级浏览归因：content_views

Revision ID: add_content_views
Revises: add_announcement_publish_at
Create Date: 2026-10-27

为什么需要这条迁移：
    漏斗（`/admin/funnel`）能回答「哪一段在流失」，但回答不了**「哪篇内容带来了转化」**。
    `tutorials.view_count` 只是聚合数，没有用户维度，无法和「后来申请了认证」关联。

设计说明：
    - **按天去重**：唯一键 `(user_id, target_type, target_id, view_date)`。
      同一用户同一天反复看同一篇只记一条 —— 既控制体量，也避免「刷自己的浏览」污染归因。
    - **只记登录用户**：游客没有稳定标识（不引入指纹），记了也无法归因；
      `user_id` 因此是 NOT NULL。
    - **不加外键**：目标表有 5 张（多态），单列外键表达不了；且内容被删后归因记录
      仍应保留（它解释的是历史行为）。
    - 写入走 `INSERT IGNORE`（唯一键冲突即忽略），**失败绝不影响详情页打开**。

⚠️ 迁移 head 变化：`tests/test_admin_audit.py` 里硬编码了 expected head（刻意棘轮），
    本文件已同步改为 `add_content_views`。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_content_views'
down_revision: Union[str, None] = 'add_announcement_publish_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'content_views',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False, comment='主键'),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='浏览用户ID'),
        sa.Column('target_type', sa.String(length=20), nullable=False, comment='内容类型: tutorial/resource/node/plugin'),
        sa.Column('target_id', sa.BigInteger(), nullable=False, comment='内容ID'),
        sa.Column('view_date', sa.Date(), nullable=False, comment='浏览日期(按天去重)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'),
                  nullable=False, comment='首次浏览时间'),
        sa.PrimaryKeyConstraint('id'),
        # 按天去重：同一人同一天看同一篇只记一条
        sa.UniqueConstraint('user_id', 'target_type', 'target_id', 'view_date',
                            name='uq_content_view_daily'),
    )
    # 归因报表：按内容聚合 + 时间窗口过滤
    op.create_index(op.f('ix_content_views_target'), 'content_views',
                    ['target_type', 'target_id', 'view_date'], unique=False)
    # 保留期清理
    op.create_index(op.f('ix_content_views_view_date'), 'content_views', ['view_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_content_views_view_date'), table_name='content_views')
    op.drop_index(op.f('ix_content_views_target'), table_name='content_views')
    op.drop_table('content_views')
