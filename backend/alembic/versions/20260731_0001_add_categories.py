"""add categories

Revision ID: add_categories
Revises: dace17c39324
Create Date: 2026-07-31 00:00:00

创建通用分类表 categories,并从现有前端常量导入种子数据:
- tutorial: ComfyUI, Coze, Stable Diffusion, AI 视频, Prompt, 效率办公
- resource: tool, model, source, doc, other
- node: 加载, 条件控制, 采样器, VAE, Latent, 图像, 遮罩, 模型, 其他
- plugin: ComfyUI 节点, Coze 插件, 其他插件
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_categories'
down_revision: Union[str, None] = 'dace17c39324'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ========== 创建 categories 表 ==========
    op.create_table(
        'categories',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('module', sa.String(length=20), nullable=False, comment='模块: tutorial/resource/node/plugin'),
        sa.Column('name', sa.String(length=50), nullable=False, comment='分类名称'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0', comment='排序权重'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module', 'name', name='uq_category_module_name'),
    )
    op.create_index(op.f('ix_categories_module'), 'categories', ['module'], unique=False)

    # ========== 插入种子数据 ==========
    # tutorial 模块 (6 项)
    op.execute(
        "INSERT INTO categories (module, name, sort_order) VALUES "
        "('tutorial', 'ComfyUI', 1), "
        "('tutorial', 'Coze', 2), "
        "('tutorial', 'Stable Diffusion', 3), "
        "('tutorial', 'AI 视频', 4), "
        "('tutorial', 'Prompt', 5), "
        "('tutorial', '效率办公', 6)"
    )

    # resource 模块 (5 项)
    op.execute(
        "INSERT INTO categories (module, name, sort_order) VALUES "
        "('resource', 'tool', 1), "
        "('resource', 'model', 2), "
        "('resource', 'source', 3), "
        "('resource', 'doc', 4), "
        "('resource', 'other', 5)"
    )

    # node 模块 (9 项)
    op.execute(
        "INSERT INTO categories (module, name, sort_order) VALUES "
        "('node', '加载', 1), "
        "('node', '条件控制', 2), "
        "('node', '采样器', 3), "
        "('node', 'VAE', 4), "
        "('node', 'Latent', 5), "
        "('node', '图像', 6), "
        "('node', '遮罩', 7), "
        "('node', '模型', 8), "
        "('node', '其他', 9)"
    )

    # plugin 模块 (3 项)
    op.execute(
        "INSERT INTO categories (module, name, sort_order) VALUES "
        "('plugin', 'ComfyUI 节点', 1), "
        "('plugin', 'Coze 插件', 2), "
        "('plugin', '其他插件', 3)"
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_categories_module'), table_name='categories')
    op.drop_table('categories')
