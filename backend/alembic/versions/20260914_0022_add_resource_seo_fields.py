"""资源表新增 SEO 字段（seo_title / seo_description / seo_keywords）

Revision ID: add_resource_seo_fields
Revises: add_rbac
Create Date: 2026-09-14 00:00:00

变更内容：
- resources.seo_title        SEO 标题（≤200 字符，未填写时前台回落资源名称）
- resources.seo_description  SEO 描述（≤500 字符，未填写时回落资源简介）
- resources.seo_keywords      SEO 关键词（逗号分隔，≤500 字符）

用途：后台编写资源时填写，仅输出到详情页 meta 标签供搜索引擎抓取，
前台页面不展示，存量数据全部为 NULL（等价于未填写，行为不变）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'add_resource_seo_fields'
down_revision: Union[str, None] = 'add_rbac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('resources', sa.Column('seo_title', sa.String(length=200), nullable=True, comment='SEO标题'))
    op.add_column('resources', sa.Column('seo_description', sa.String(length=500), nullable=True, comment='SEO描述'))
    op.add_column('resources', sa.Column('seo_keywords', sa.String(length=500), nullable=True, comment='SEO关键词(逗号分隔)'))


def downgrade() -> None:
    op.drop_column('resources', 'seo_keywords')
    op.drop_column('resources', 'seo_description')
    op.drop_column('resources', 'seo_title')
