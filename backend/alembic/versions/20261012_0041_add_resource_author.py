"""资源新增「发布作者」与作者头像

Revision ID: add_resource_author
Revises: drop_plugin_legacy_fields
Create Date: 2026-10-12

背景（用户需求）：
    资源卡片要展示「预览量 + 发布作者(头像)」，与扩展卡片同一形态。
    资源此前没有作者字段（`view_count` 早已存在且已对外返回，无需改动）。

两列都可空，理由与扩展（`20261012_0039_add_plugin_author`）一致：
    - 历史数据没有作者，非空会强制运营逐条补录；
    - 语义上「未填写」就该是 NULL，前台据此**不渲染作者行**，而不是显示一个空名字。

无索引：这两列只随行返回，不参与筛选/排序。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_resource_author'
down_revision: Union[str, None] = 'drop_plugin_legacy_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """存在性判断：MySQL DDL 非事务，中断后原地重跑必须能跳过已完成的步骤（R5-09）"""
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def upgrade() -> None:
    if not _column_exists('resources', 'author_name'):
        op.add_column(
            'resources',
            sa.Column('author_name', sa.String(100), nullable=True, comment='发布作者'),
        )
    if not _column_exists('resources', 'author_avatar_url'):
        op.add_column(
            'resources',
            sa.Column('author_avatar_url', sa.String(500), nullable=True, comment='作者头像URL'),
        )


def downgrade() -> None:
    if _column_exists('resources', 'author_avatar_url'):
        op.drop_column('resources', 'author_avatar_url')
    if _column_exists('resources', 'author_name'):
        op.drop_column('resources', 'author_name')
