"""add_content_to_resource_and_node

Revision ID: d12b83560671
Revises: 20260722_0001
Create Date: 2026-07-22 01:20:38.289664

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd12b83560671'
down_revision: Union[str, None] = '20260722_0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('resources', sa.Column('content', sa.Text(), nullable=True, comment='正文内容(Markdown/HTML)'))
    op.add_column('nodes', sa.Column('content', sa.Text(), nullable=True, comment='正文内容(Markdown/HTML)'))


def downgrade() -> None:
    op.drop_column('nodes', 'content')
    op.drop_column('resources', 'content')
