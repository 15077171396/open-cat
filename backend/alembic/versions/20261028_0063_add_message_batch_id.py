"""站内消息：`messages` 增加 `batch_id`（群发批次号）

Revision ID: add_message_batch_id
Revises: add_feedback_images
Create Date: 2026-10-28

为什么需要：
    管理端要能「给全站发系统通知」。一次群发会给每个用户插一行 `messages`，
    没有批次号的话，后台既无法展示「发过哪些通知」，也无法统计「送达多少、读了多少」，
    只能对着一万行逐条数。

    评论回复、内容下架这类**单条**自动通知 `batch_id` 为 NULL —— 它们本来就不成批，
    后台的「已发通知」列表也只列批次（`batch_id IS NOT NULL`），两者互不干扰。

索引：后台按 `batch_id` 分组聚合 + 未来可能的「按批次撤回」，走 `ix_messages_batch_id`。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_message_batch_id"
down_revision: Union[str, None] = "add_feedback_images"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("batch_id", sa.String(length=32), nullable=True, comment="群发批次号（单条通知为空）"),
    )
    op.create_index("ix_messages_batch_id", "messages", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_batch_id", table_name="messages")
    op.drop_column("messages", "batch_id")
