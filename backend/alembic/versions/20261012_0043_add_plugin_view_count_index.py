"""补齐 plugins.view_count 的排序索引

Revision ID: add_plugin_view_count_index
Revises: add_node_cover_and_author
Create Date: 2026-10-12

为什么需要（第五轮审查 R5-07，是 `20261010_0035_add_list_sort_indexes` 的回退）：
    0035 的立论原话是「多个公开列表接口按热度排序，但排序键在对应表上没有任何索引
    → 每次翻页都走 filesort」，它据此为 plugins 建了 `(install_count)` 与
    `(rating, install_count)`。本批 `20261012_0040` 把这两列连同那两个索引一起删掉，
    而**接替它们的排序键 `view_count` 没有任何索引** —— 删旧索引和建新索引分居两条迁移、
    且后者从未被写出来，等于把 0035 的修复原地撤销。

受影响的路径（都走 `ORDER BY view_count DESC, id DESC`）：
    - `GET /api/v1/plugins` 默认（也是唯一剩下的非时间）排序 —— **无 Redis 缓存**，
      每次请求与每次无限滚动翻页都要付这笔钱；
    - 首页聚合 `api/home.py` 直接调 `list_plugins(db, 1, 5)`；
    - `get_featured_plugins` 平时被 300s 缓存挡着，但它的降级语义就是「Redis 不可用直查 DB」
      —— 缓存层出问题的当下正好把这条无索引排序打成每次请求；
    - `llms_txt_service` 的热门扩展段。

实测（本机 dev 库，`plugins` 仅 10 行）：

    EXPLAIN SELECT id,name FROM plugins ORDER BY view_count DESC, id DESC LIMIT 8
    -> type=ALL  key=NULL  Extra="Using filesort"

单主机上 MySQL 与 gunicorn/prerender 抢同一台机的 IO，内容量从几十涨到几千后，
「扩展页一打开，整站 DB 都慢」就是 0035 当初要防的那个故障。

索引形态与查询对齐：查询是 `view_count DESC, id DESC`，故建 `(view_count, id)`；
`models/node.py` 的 `Plugin.view_count` 同步补 `index=True`，避免 autogenerate 反向 DROP。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_plugin_view_count_index'
down_revision: Union[str, None] = 'add_node_cover_and_author'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = 'ix_plugins_view_count'


def _index_exists(table: str, index: str) -> bool:
    """存在性判断：与同批迁移一致，中断后可原地重跑（R5-09）"""
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i LIMIT 1"
        ),
        {"t": table, "i": index},
    ).first()
    return row is not None


def upgrade() -> None:
    if not _index_exists('plugins', INDEX_NAME):
        op.create_index(INDEX_NAME, 'plugins', ['view_count', 'id'])
        print(f"[add_plugin_view_count_index] 已建 {INDEX_NAME} (view_count, id)")


def downgrade() -> None:
    if _index_exists('plugins', INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name='plugins')
