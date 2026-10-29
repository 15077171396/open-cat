"""公告定时发布：announcements 增加 publish_at

Revision ID: add_announcement_publish_at
Revises: add_user_totp
Create Date: 2026-10-27

为什么需要这条迁移：
    公告此前只有 `is_published` 一个开关，**活动公告只能人工卡点上线** ——
    要么提前发（用户看到过期的「明晚 8 点」），要么到点手动点发布（人不在就错过）。

语义（刻意不引入新状态）：
    - `publish_at IS NULL` → 沿用原语义（`is_published` 决定可见性）；
    - `publish_at <= now`  → 已到点，正常可见；
    - `publish_at > now`   → **即使 is_published=1 也不可见**（到点自动出现）。
    因此**不需要定时任务**：可见性在查询时判断，不依赖「有人记得跑一次」。
    代价是「到点那一刻」由请求触发，秒级误差可接受（公告不是交易）。

⚠️ 迁移 head 变化：`tests/test_admin_audit.py` 里硬编码了 expected head（刻意棘轮），
    本文件已同步改为 `add_announcement_publish_at`。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_announcement_publish_at'
down_revision: Union[str, None] = 'add_user_totp'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'announcements',
        sa.Column('publish_at', sa.DateTime(), nullable=True,
                  comment='定时发布时间(NULL=立即, >now=未到点不可见)'),
    )


def downgrade() -> None:
    # 回滚会丢掉所有定时设置：原本「未到点」的公告会立刻变成可见
    # （因为过滤条件消失，只剩 is_published）。降级前请先撤下这些公告。
    op.drop_column('announcements', 'publish_at')
