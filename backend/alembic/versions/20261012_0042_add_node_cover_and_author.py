"""百科（node）新增封面图 + 发布作者 + 作者头像

Revision ID: add_node_cover_and_author
Revises: add_resource_author
Create Date: 2026-10-12

背景（用户需求）：
    百科列表卡片要改布局：左侧 1/3 放首图（封面），右侧 2/3 放文字，
    底部浏览/时间在左、发布作者(头像)在最右。封面与作者字段都不存在。
    三列都可空，为空时前台**不渲染**对应区域（NULL = 未填写，不显示空名字/破图）。

无索引：仅随行返回，不参与筛选/排序。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_node_cover_and_author'
down_revision: Union[str, None] = 'add_resource_author'
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


NEW_COLUMNS = (
    ('cover_image', sa.String(500), '封面图URL'),
    ('author_name', sa.String(100), '发布作者'),
    ('author_avatar_url', sa.String(500), '作者头像URL'),
)


def upgrade() -> None:
    for name, coltype, comment in NEW_COLUMNS:
        if not _column_exists('nodes', name):
            op.add_column('nodes', sa.Column(name, coltype, nullable=True, comment=comment))


def downgrade() -> None:
    for name, _coltype, _comment in NEW_COLUMNS:
        if _column_exists('nodes', name):
            op.drop_column('nodes', name)
