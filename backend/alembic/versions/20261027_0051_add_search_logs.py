"""站内搜索词日志：search_logs

Revision ID: add_search_logs
Revises: add_content_soft_delete
Create Date: 2026-10-27

为什么需要这条迁移：
    站内搜索此前**不记录查询词**。而「用户搜了什么、搜不到什么」是内容站最便宜的
    选题来源 —— 零结果词直接就是「缺哪篇内容」的清单。没有这张表，运营只能靠猜。

设计说明：
    - **不存 user_id**：本表只用于「热度 + 零结果 + 模块分布」三类聚合，
      不需要按人下钻；不存用户标识顺带避免把搜索词变成可回溯到个人的数据
      （搜索词常含隐私意图，能少存就少存）。
    - `keyword` 存**清洗后的原始词**（去首尾空格、按 SEARCH_MAX_KEYWORD_LEN 截断），
      不做归一化/分词：归一化会让「ComfyUI 插件」和「comfyui插件」合并，
      但也会让运营看不到用户真实的输入形态（而这正是选标题的依据）。
    - 索引：`created_at` 支撑「近 N 天」范围扫描；`(keyword, created_at)` 支撑按词聚合。
      数据量按「一次搜索一行」增长，保留期 90 天（见 scheduler 的清理任务），
      2 核 4G 上完全够用。

⚠️ 迁移 head 变化：`tests/test_admin_audit.py` 里硬编码了 expected head（刻意棘轮），
    本文件已同步改为 `add_search_logs`。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_search_logs'
down_revision: Union[str, None] = 'add_content_soft_delete'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'search_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False, comment='主键'),
        sa.Column('keyword', sa.String(length=64), nullable=False, comment='搜索词(清洗后原始输入)'),
        sa.Column('module', sa.String(length=20), nullable=False, comment='检索模块: tutorial/resource/node/plugin/tool'),
        sa.Column('result_count', sa.Integer(), nullable=False, server_default='0', comment='本次命中条数(0=零结果)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='搜索时间'),
        sa.PrimaryKeyConstraint('id'),
    )
    # 「近 N 天」范围扫描 + 保留期清理都靠它
    op.create_index(op.f('ix_search_logs_created_at'), 'search_logs', ['created_at'], unique=False)
    # 按词聚合（热搜榜 / 零结果榜）
    op.create_index(op.f('ix_search_logs_keyword_created'), 'search_logs', ['keyword', 'created_at'], unique=False)
    # 零结果榜：只扫 result_count=0 的行，避免全表
    op.create_index(op.f('ix_search_logs_result_count'), 'search_logs', ['result_count'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_search_logs_result_count'), table_name='search_logs')
    op.drop_index(op.f('ix_search_logs_keyword_created'), table_name='search_logs')
    op.drop_index(op.f('ix_search_logs_created_at'), table_name='search_logs')
    op.drop_table('search_logs')
