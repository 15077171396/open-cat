"""tutorials 新增 introduction 课程介绍字段

Revision ID: add_tutorial_intro
Revises: content_mediumtext
Create Date: 2026-08-17

教程介绍页独立展示课程介绍(富文本 HTML), 与简介 description 分离。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'add_tutorial_intro'
down_revision: Union[str, None] = 'content_mediumtext'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tutorials',
        sa.Column(
            'introduction',
            sa.Text(length=16777215),
            nullable=True,
            comment='课程介绍(HTML)',
        ),
    )


def downgrade() -> None:
    op.drop_column('tutorials', 'introduction')
