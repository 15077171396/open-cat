"""新增反馈回复表 feedback_replies（反馈工单化：管理员回复用户可见）

Revision ID: add_feedback_replies
Revises: add_positions
Create Date: 2026-11-03

与 Feedback.admin_note（内部备注，用户不可见）职责分离：回复进入对话线程、
用户可见，sender 区分 admin/user。回复通知走 messages 表（同事务写入）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_feedback_replies"
down_revision: Union[str, None] = "add_positions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback_replies",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("feedback_id", sa.BigInteger(), nullable=False, comment="所属反馈ID"),
        sa.Column("sender", sa.String(length=10), nullable=False, comment="发送方: admin/user"),
        sa.Column("content", sa.Text(), nullable=False, comment="回复内容"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # 线程按时间升序读：详情页一次查询取全部回复
    op.create_index(
        "ix_feedback_replies_feedback_id_created_at",
        "feedback_replies",
        ["feedback_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_feedback_replies_feedback_id_created_at", table_name="feedback_replies"
    )
    op.drop_table("feedback_replies")
