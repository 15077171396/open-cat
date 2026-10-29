"""图片资产台账：图库导入、上传与 AI 图片统一索引

Revision ID: add_image_assets
Revises: add_ai_writing
Create Date: 2026-10-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_image_assets"
down_revision: Union[str, None] = "add_ai_writing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "image_assets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("source", sa.String(20), nullable=False, comment="图源"),
        sa.Column("source_id", sa.String(64), nullable=False, comment="图源图片 ID"),
        sa.Column("sha256", sa.String(64), nullable=False, comment="内容 sha256"),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("alt", sa.String(300), nullable=False, server_default=""),
        sa.Column("author", sa.String(200), nullable=False, server_default=""),
        sa.Column("author_url", sa.String(1000), nullable=True),
        sa.Column("page_url", sa.String(1000), nullable=True),
        sa.Column("license", sa.String(60), nullable=False, server_default=""),
        sa.Column("license_url", sa.String(1000), nullable=True),
        sa.Column("requires_attribution", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cos_url", sa.String(1000), nullable=False),
        sa.Column("thumb_url", sa.String(1000), nullable=True),
        sa.Column("query", sa.String(200), nullable=False, server_default=""),
        sa.Column("tags", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "source_id", name="uq_image_assets_source_source_id"),
    )
    op.create_index("ix_image_assets_sha256", "image_assets", ["sha256"])
    op.create_index("ix_image_assets_is_active", "image_assets", ["is_active"])
    op.create_index("ix_image_assets_created_at", "image_assets", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_image_assets_created_at", table_name="image_assets")
    op.drop_index("ix_image_assets_is_active", table_name="image_assets")
    op.drop_index("ix_image_assets_sha256", table_name="image_assets")
    op.drop_table("image_assets")
