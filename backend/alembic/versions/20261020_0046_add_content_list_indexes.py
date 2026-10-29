"""为公开列表的「发布态 + 排序键」补复合索引

Revision ID: add_content_list_indexes
Revises: add_content_publish_state
Create Date: 2026-10-20

为什么现在补（第五轮审查 PERF-01）：
    `add_content_publish_state` 当时刻意没建索引，理由是「等真有慢查询证据再补」。
    但公开列表的查询形态是固定的：`WHERE is_published = 1 ORDER BY <排序键> DESC LIMIT n`
    —— 没有以 is_published 打头的复合索引时，MySQL 只能全表扫 + filesort：
      · 分页的 `COUNT(*)` 是纯全表扫描（COUNT 不走 LIMIT，无法提前收敛）；
      · 列表查询要靠 filesort 排序，数据量越大越慢。
    `tutorials` / `tools` 早就有 `is_published` 单列索引，所以只有这三张表是裸的。

索引与真实排序键一一对应（不要照抄，改了 order_by 就要同步改这里）：
    nodes      latest  → updated_at DESC, id DESC      → (is_published, updated_at)
               hottest / recommended → view_count DESC → (is_published, view_count)
    plugins    latest  → created_at DESC, id DESC      → (is_published, created_at)
               默认（comprehensive）→ view_count DESC  → (is_published, view_count)
    resources  置顶/推荐优先，再按 updated_at 或 download_count
               → (is_published, is_pinned, is_featured, updated_at)
               → (is_published, is_pinned, is_featured, download_count)
    tools      列表按 created_at DESC（单列 is_published 索引已存在，补复合避免 filesort）
               → (is_published, created_at)

    二级索引末尾隐含主键（InnoDB），所以 `ORDER BY x DESC, id DESC` 能直接用
    `(is_published, x)` 满足，不必把 id 显式写进索引。

成本：MySQL 8 的 CREATE INDEX 对 InnoDB 是 ONLINE DDL（INPLACE / LOCK=NONE），
线上建索引不锁表；当前内容量在百级，每条索引构建都是毫秒级。
"""

from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
# ⚠️ 必须写成带类型注解的形式：tests/test_admin_audit.py 用正则解析
#    `revision: str = '...'` / `down_revision: Union[str, None] = '...'`，
#    写成裸赋值会被静默跳过，导致「迁移数 != 文件数」而报 head 不对。
revision: str = 'add_content_list_indexes'
down_revision: Union[str, None] = 'add_content_publish_state'
branch_labels = None
depends_on = None


# (表, 索引名, 列)
_INDEXES = [
    ("nodes", "ix_nodes_published_updated", ["is_published", "updated_at"]),
    ("nodes", "ix_nodes_published_views", ["is_published", "view_count"]),
    ("plugins", "ix_plugins_published_created", ["is_published", "created_at"]),
    ("plugins", "ix_plugins_published_views", ["is_published", "view_count"]),
    (
        "resources",
        "ix_resources_published_pinned_featured_updated",
        ["is_published", "is_pinned", "is_featured", "updated_at"],
    ),
    (
        "resources",
        "ix_resources_published_pinned_featured_downloads",
        ["is_published", "is_pinned", "is_featured", "download_count"],
    ),
    ("tools", "ix_tools_published_created", ["is_published", "created_at"]),
]


def upgrade() -> None:
    for table, name, columns in _INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    for table, name, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
