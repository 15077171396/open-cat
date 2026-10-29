"""扩展新增「发布作者」与作者头像

Revision ID: add_plugin_author
Revises: generalize_comment_targets
Create Date: 2026-10-12

背景（用户需求）：
    扩展列表卡片原先展示「安装量」，用户要求改为展示「预览量」，并补上
    **发布作者与头像**。作者此前没有任何持久化字段 —— 第三方导入接口会返回
    `author`（仓库类为 owner），但那只用于管理端表单的临时回填，关掉页面就没了。

两列都可空，理由：
    - 历史数据没有作者，非空会强制运营逐条补录；
    - 语义上「未填写」就该是 NULL（与 seo_* 同惯例），前台据此**不渲染作者行**，
      而不是显示一个空名字。

无索引：这两列只随行返回，不参与筛选/排序。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_plugin_author'
down_revision: Union[str, None] = 'generalize_comment_targets'
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
    if not _column_exists('plugins', 'author_name'):
        op.add_column(
            'plugins',
            sa.Column('author_name', sa.String(100), nullable=True, comment='发布作者'),
        )
    if not _column_exists('plugins', 'author_avatar_url'):
        op.add_column(
            'plugins',
            sa.Column('author_avatar_url', sa.String(500), nullable=True, comment='作者头像URL'),
        )


def downgrade() -> None:
    if _column_exists('plugins', 'author_avatar_url'):
        op.drop_column('plugins', 'author_avatar_url')
    if _column_exists('plugins', 'author_name'):
        op.drop_column('plugins', 'author_name')
