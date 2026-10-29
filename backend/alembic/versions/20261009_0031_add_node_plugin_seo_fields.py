"""知识百科（nodes）/ 插件（plugins）新增 SEO 字段（台账 P1-17）

Revision ID: add_node_plugin_seo_fields
Revises: seed_comment_permissions
Create Date: 2026-10-09

背景：
    台账 P1-10（新建「知识库/博客」板块）经核实与现有「知识百科」（``nodes`` 模块）
    **重复建设**，已关闭；复查「用现有 nodes 承载问题型长尾内容」是否可行时发现：
    ``seo_title`` / ``seo_description`` / ``seo_keywords`` 只有 ``resources`` 与
    ``tutorials`` 有（迁移 0022 / 0023），而 ``nodes`` / ``plugins`` 没有——
    偏偏这两个模块正是 SEO 方案 10.2 / 10.5 指定的栏目词承载页
    （「ComfyUI 节点」「ComfyUI 插件下载」）。没有自定义 TDK 就只能吃模板拼接的标题，
    长尾词覆盖能力直接受限，且管理端没有对应编辑项。

变更内容（与 0022 / 0023 完全同名同宽，保持四张内容表口径一致）：
- nodes.seo_title / nodes.seo_description / nodes.seo_keywords
- plugins.seo_title / plugins.seo_description / plugins.seo_keywords

用途：后台编辑节点/插件时填写，仅输出到详情页 meta 标签供搜索引擎抓取，
前台页面不展示；存量数据全部为 NULL（等价于未填写，行为完全不变）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_node_plugin_seo_fields'
down_revision: Union[str, None] = 'seed_comment_permissions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = ("nodes", "plugins")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("seo_title", sa.String(length=200), nullable=True, comment="SEO标题"),
        )
        op.add_column(
            table,
            sa.Column(
                "seo_description", sa.String(length=500), nullable=True, comment="SEO描述"
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "seo_keywords",
                sa.String(length=500),
                nullable=True,
                comment="SEO关键词(逗号分隔)",
            ),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "seo_keywords")
        op.drop_column(table, "seo_description")
        op.drop_column(table, "seo_title")
