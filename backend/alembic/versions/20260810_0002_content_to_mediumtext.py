"""nodes/plugins content 列升级为 MEDIUMTEXT

Revision ID: content_mediumtext
Revises: add_categories
Create Date: 2026-08-10

富文本正文 (含图片等富标签) 易超 TEXT 64KB 上限导致保存失败,
与 tutorials.content 对齐升级为 MEDIUMTEXT (16MB)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'content_mediumtext'
down_revision: Union[str, None] = 'add_categories'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# MySQL MODIFY COLUMN 需保留完整列定义
_TABLES = [
    (
        'nodes',
        'content',
        '正文内容(Markdown/HTML)',
    ),
    (
        'plugins',
        'content',
        '正文内容(Markdown/HTML)',
    ),
]


def upgrade() -> None:
    for table, column, comment in _TABLES:
        op.execute(
            f"ALTER TABLE {table} MODIFY COLUMN {column} "
            f"MEDIUMTEXT NULL COMMENT '{comment}'"
        )


def downgrade() -> None:
    for table, column, comment in _TABLES:
        op.execute(
            f"ALTER TABLE {table} MODIFY COLUMN {column} "
            f"TEXT NULL COMMENT '{comment}'"
        )
