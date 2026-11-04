"""站内聊天三张表：chat_conversations / chat_messages / chat_members

Revision ID: add_chat_tables
Revises: seed_ai_playground_permission
Create Date: 2026-11-04

管理员之间的轻量内部沟通（私聊 / 群聊，纯文字 + WS 推送 + 未读角标）。
chat_messages 主键自增（排序与已读游标依赖单调性），理由见 models/chat.py。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_chat_tables"
down_revision: Union[str, None] = "seed_ai_playground_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("type", sa.String(length=10), nullable=False, comment="direct/group"),
        sa.Column("title", sa.String(length=100), nullable=True, comment="群聊标题(单聊为空)"),
        sa.Column("created_by", sa.BigInteger(), nullable=False, comment="发起人ID"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False,
            comment="最近消息时间",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 消息主键 AUTO_INCREMENT：单调查询/游标的正确性优先于全库的防枚举惯例
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False, comment="所属会话ID"),
        sa.Column("sender_id", sa.BigInteger(), nullable=False, comment="发送人ID"),
        sa.Column("content", sa.Text(), nullable=False, comment="消息内容(纯文字)"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_chat_messages_conv_id", "chat_messages", ["conversation_id", "id"]
    )

    op.create_table(
        "chat_members",
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("last_read_id", sa.BigInteger(), nullable=False, server_default=sa.text("0"),
                  comment="已读游标(消息id)"),
        sa.Column("joined_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id", "user_id"),
    )
    op.create_index(
        "ix_chat_members_user_conv", "chat_members", ["user_id", "conversation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_members_user_conv", table_name="chat_members")
    op.drop_table("chat_members")
    op.drop_index("ix_chat_messages_conv_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("chat_conversations")
