"""AI 写作：模型模态/图片单价 + 操作提示词表

Revision ID: add_ai_writing
Revises: seed_message_permission
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_ai_writing"
down_revision: Union[str, None] = "seed_message_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_models", sa.Column("modality", sa.String(10), nullable=False,
                                          server_default="text", comment="模态：text / image"))
    op.add_column("ai_models", sa.Column("image_price", sa.Numeric(12, 4), nullable=False,
                                          server_default="0", comment="图片单价（元/张）"))
    op.create_index("ix_ai_models_modality", "ai_models", ["modality"])
    op.create_table(
        "ai_writing_prompts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("display_name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_template", sa.Text(), nullable=False),
        sa.Column("output_type", sa.String(10), nullable=False, server_default="markdown"),
        sa.Column("model_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("temperature", sa.Numeric(3, 2), nullable=False, server_default="0.7"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("max_input_chars", sa.Integer(), nullable=False, server_default="12000"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    # 与模型定义 unique=True, index=True 对齐：唯一索引而不是额外再加唯一约束
    op.create_index("ix_ai_writing_prompts_operation", "ai_writing_prompts", ["operation"], unique=True)
    op.create_index("ix_ai_writing_prompts_is_enabled", "ai_writing_prompts", ["is_enabled"])


def downgrade() -> None:
    op.drop_index("ix_ai_writing_prompts_is_enabled", table_name="ai_writing_prompts")
    op.drop_index("ix_ai_writing_prompts_operation", table_name="ai_writing_prompts")
    op.drop_table("ai_writing_prompts")
    op.drop_index("ix_ai_models_modality", table_name="ai_models")
    op.drop_column("ai_models", "image_price")
    op.drop_column("ai_models", "modality")
