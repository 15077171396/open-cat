"""素材库：图片资产扩展为图片/文件统一素材台账

Revision ID: add_material_fields
Revises: add_image_assets
Create Date: 2026-10-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_material_fields"
down_revision: Union[str, None] = "add_image_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("image_assets", sa.Column("kind", sa.String(20), nullable=False,
                                            server_default="image", comment="素材类型"))
    op.add_column("image_assets", sa.Column("mime_type", sa.String(100), nullable=False,
                                            server_default="", comment="MIME 类型"))
    op.add_column("image_assets", sa.Column("filename", sa.String(300), nullable=False,
                                            server_default="", comment="原始文件名"))
    op.add_column("image_assets", sa.Column("usage_count", sa.Integer(), nullable=False,
                                            server_default="0", comment="使用次数"))
    op.add_column("image_assets", sa.Column("last_used_at", sa.DateTime(), nullable=True,
                                            comment="最近使用时间"))
    op.create_index("ix_image_assets_kind", "image_assets", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_image_assets_kind", table_name="image_assets")
    op.drop_column("image_assets", "last_used_at")
    op.drop_column("image_assets", "usage_count")
    op.drop_column("image_assets", "filename")
    op.drop_column("image_assets", "mime_type")
    op.drop_column("image_assets", "kind")
