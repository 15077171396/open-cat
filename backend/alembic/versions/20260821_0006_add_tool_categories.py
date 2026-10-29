"""add tool module categories

Revision ID: add_tool_categories
Revises: add_free_started_at
Create Date: 2026-08-21 00:00:00

工具模块接入通用分类管理（categories 表新增 tool 模块种子数据）:
- tool: Prompt, 图像, ComfyUI, 计算, 数据可视化（与 seed_more.py 中工具的分类值对齐）
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'add_tool_categories'
down_revision: Union[str, None] = 'add_free_started_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tool 模块 (5 项)
    op.execute(
        "INSERT INTO categories (module, name, sort_order) VALUES "
        "('tool', 'Prompt', 1), "
        "('tool', '图像', 2), "
        "('tool', 'ComfyUI', 3), "
        "('tool', '计算', 4), "
        "('tool', '数据可视化', 5)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM categories WHERE module = 'tool'")
