"""教程评论支持回复与配图

Revision ID: add_comment_reply_images
Revises: add_list_sort_indexes
Create Date: 2026-10-10

背景（用户需求）：
    评论初版刻意只做「一层平铺」，理由是怕「父评论被删后子评论怎么显示」变成
    新的孤儿数据问题（P1-7 已踩过同类坑）。现按需求补上回复与配图，
    取舍如下：

    1. **只做两级**（顶层 + 回复）：回复的回复一律挂到同一个顶层父评论下。
       三级以上在移动端没有可用的展示形态，且层级归属无法解释。
       故本迁移**不加自引用外键**——`parent_id` 只允许指向顶层评论，
       该约束由 `comment_service.create_comment` 在写入时校验
       （加外键反而会在教程硬删除清理时触发 1451，与本表既有的无外键设计冲突）。
    2. **父评论被删 → 连带软删其回复**：列表因此永远不需要渲染
       「挂在不存在的父评论下的回复」，从根上避免孤儿数据。
    3. **配图存 URL 列表（JSON）**：只接受本站 COS/CDN 域名下的地址，
       避免评论区被当成任意图床或追踪像素的载体。

新增索引 `ix_tc_parent`：列表查询会「按一批顶层评论 id 取回复」，无索引会全表扫。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_comment_reply_images'
down_revision: Union[str, None] = 'add_list_sort_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tutorial_comments',
        sa.Column(
            'parent_id',
            sa.BigInteger(),
            nullable=True,
            comment='父评论ID(NULL=顶层, 仅两级)',
        ),
    )
    op.add_column(
        'tutorial_comments',
        sa.Column(
            'images',
            sa.JSON(),
            nullable=True,
            comment='配图URL列表(最多3张)',
        ),
    )
    op.create_index('ix_tc_parent', 'tutorial_comments', ['parent_id'])


def downgrade() -> None:
    op.drop_index('ix_tc_parent', table_name='tutorial_comments')
    op.drop_column('tutorial_comments', 'images')
    op.drop_column('tutorial_comments', 'parent_id')
