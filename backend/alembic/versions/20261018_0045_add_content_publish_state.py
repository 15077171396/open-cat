"""内容发布状态：nodes / plugins / resources 增加 is_published

Revision ID: add_content_publish_state
Revises: add_content_author_id
Create Date: 2026-10-18

为什么需要：
    教程（tutorials）与工具（tools）早就有 `is_published`，但**百科 / 扩展 / 资源三张表
    一直没有** —— 后台保存即公开，既没有草稿态，也没有下架能力（发现问题只能删掉重建）。

    在「全领域铺内容 + 第三方导入」的节奏下这会持续制造事故：导入的正文还没润色，
    它已经出现在首页和列表页了；而首页又是缓存 60 秒的聚合接口，连回滚都有延迟。

    本次给三张表补上与教程/工具**同名同语义**的 `is_published`：
    - 1 = 已发布 → 前台列表 / 详情 / 首页 / sitemap 可见
    - 0 = 草稿   → 仅后台可见，公开接口一律过滤掉

存量数据：
    `server_default='1'` —— 迁移瞬间所有已有内容都视为已发布，**线上展示零变化**。
    这是刻意选择：若默认 0，本次部署会让线上所有百科/扩展/资源同时消失。

为什么不建索引：
    三张表当前分别只有约 30 / 14 / 20 行，`is_published` 又是极低基数列（绝大多数为 1），
    单列索引基本不会被优化器选中；复合索引（is_published + 各表排序键）需要按三种不同的
    排序形态分别设计，等内容量上来、真的有慢查询证据时再补 —— 现在先建就是三个需要长期
    维护却暂时没用的形态。参考：`tools.is_published` 有索引，但它的查询形态不同，不能类比。

幂等：与 0044 同口径，每步先查 information_schema，中断后可原地重跑。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'add_content_publish_state'
down_revision: Union[str, None] = 'add_content_author_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = ("nodes", "plugins", "resources")
_COLUMN = "is_published"


def _offline() -> bool:
    """`alembic upgrade --sql` 模式拿不到可查询的连接，直接产出语句"""
    return bool(op.get_context().as_sql)


def _column_exists(table: str) -> bool:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c LIMIT 1"
        ),
        {"t": table, "c": _COLUMN},
    ).first()
    return row is not None


def upgrade() -> None:
    for table in _TABLES:
        if not _offline() and _column_exists(table):
            print(f"[add_content_publish_state] 跳过已存在的 {table}.{_COLUMN}")
            continue
        op.add_column(
            table,
            sa.Column(
                _COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
                comment="是否已发布（0=草稿，前台不可见）",
            ),
        )
        print(f"[add_content_publish_state] 已加 {table}.{_COLUMN}（存量默认 1）")


def downgrade() -> None:
    # 逆序删除，与 upgrade 严格镜像（三表互不依赖，逆序只为对称可读）
    for table in reversed(_TABLES):
        if not _offline() and not _column_exists(table):
            print(f"[add_content_publish_state] 跳过不存在的 {table}.{_COLUMN}")
            continue
        op.drop_column(table, _COLUMN)
