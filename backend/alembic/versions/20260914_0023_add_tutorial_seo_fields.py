"""教程表新增 SEO 字段（seo_title / seo_description / seo_keywords）

Revision ID: add_tutorial_seo_fields
Revises: add_resource_seo_fields
Create Date: 2026-09-14 00:00:00

变更内容：
- tutorials.seo_title        SEO 标题（≤200 字符，未填写时前台回落默认标题格式）
- tutorials.seo_description  SEO 描述（≤500 字符，未填写时回落教程简介）
- tutorials.seo_keywords      SEO 关键词（逗号分隔，≤500 字符）

用途：后台编辑教程时填写，仅输出到教程介绍页与章节阅读页的 meta 标签
供搜索引擎抓取，前台页面不展示；存量数据全部为 NULL（等价于未填写，行为不变）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'add_tutorial_seo_fields'
down_revision: Union[str, None] = 'add_resource_seo_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tutorials', sa.Column('seo_title', sa.String(length=200), nullable=True, comment='SEO标题'))
    op.add_column('tutorials', sa.Column('seo_description', sa.String(length=500), nullable=True, comment='SEO描述'))
    op.add_column('tutorials', sa.Column('seo_keywords', sa.String(length=500), nullable=True, comment='SEO关键词(逗号分隔)'))


def downgrade() -> None:
    op.drop_column('tutorials', 'seo_keywords')
    op.drop_column('tutorials', 'seo_description')
    op.drop_column('tutorials', 'seo_title')
