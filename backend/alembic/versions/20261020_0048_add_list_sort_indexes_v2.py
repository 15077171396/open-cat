"""补齐「教程列表 / 资源按下载量」的排序索引（第二轮复审 C-06 / P-01）

Revision ID: add_list_sort_indexes_v2
Revises: add_tools_fulltext
Create Date: 2026-10-20

背景：
    0046 给 nodes / plugins / resources / tools 补了「发布态 + 排序键」复合索引，
    但漏了两处：

    1. **tutorials 完全没覆盖**。教程列表的真实查询是
       ``WHERE is_published = 1 ORDER BY is_pinned DESC, is_recommended DESC,
       published_at DESC, id DESC``（tutorial_service.py），而模型上现有的三条索引
       分别以 ``is_published`` 或 ``is_pinned`` 打头，没有一个能同时满足「过滤 + 排序」：
         · ix_tutorials_published_at (is_published, published_at)
           → 过滤能走索引，但 ORDER BY 里有 is_pinned / is_recommended，仍要 filesort；
         · ix_tutorials_pin_reco_published (is_pinned, is_recommended, published_at)
           → 排序能走索引，但 is_published 不在前缀，只能扫全部行再回表过滤。
       草稿越多，后者的读放大越明显。

    2. **resources 的下载量排序仍会 filesort**。0046 建的
       ``(is_published, is_pinned, is_featured, download_count)`` 只覆盖到
       ``download_count``；而实际排序还有 ``updated_at DESC, id DESC`` 兜底
       （同下载量的资源必须稳定排序，否则翻页会重复/漏项）。
       把 ``updated_at`` 续到索引末尾即可让整条 ORDER BY 走索引顺序。

列方向说明：
    三处 ORDER BY 全部是 DESC，且列之间方向一致，MySQL 8 可以对全 ASC 的索引做
    反向扫描（backward index scan），不需要显式 ``DESC`` 索引。
    若将来把某一列改成 ASC，必须改成 ``Index(..., col.desc())`` 的混合方向索引，
    否则索引失效 —— 改 order_by 时请同步改这里。

成本：当前内容量百级，建索引毫秒级；MySQL 8 的 CREATE INDEX 是 ONLINE DDL（不锁表）。
"""

from typing import Union

from alembic import op

revision: str = 'add_list_sort_indexes_v2'
down_revision: Union[str, None] = 'add_tools_fulltext'
branch_labels = None
depends_on = None


# (表, 索引名, 列)
_INDEXES = [
    # 教程列表：最新 / 最热两个排序各一条（与 tutorial_service.py 的 order_by 逐列对应）
    (
        "tutorials",
        "ix_tutorials_pub_pin_reco_at",
        ["is_published", "is_pinned", "is_recommended", "published_at"],
    ),
    (
        "tutorials",
        "ix_tutorials_pub_pin_reco_views",
        ["is_published", "is_pinned", "is_recommended", "view_count"],
    ),
    # 资源列表按下载量：续上 updated_at，消除 filesort
    (
        "resources",
        "ix_resources_pub_pin_feat_dl_updated",
        ["is_published", "is_pinned", "is_featured", "download_count", "updated_at"],
    ),
]


def upgrade() -> None:
    for table, name, columns in _INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    for table, name, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
