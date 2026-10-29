"""补齐 5 张内容表的 updated_at 索引

Revision ID: add_updated_at_indexes
Revises: add_plugin_view_count_index
Create Date: 2026-10-13

为什么需要（缺口台账 P2-43 的「索引半边」）：
    第四轮审查时 `scheduler/tasks.py::collect_recent_urls` 的 6 条查询**既无 LIMIT
    又无排序**，每天 03:00 与 03:10 各跑一次全表扫。2026-10-12 的代码侧整改给它们加了
    `ORDER BY updated_at DESC, id DESC` + 每板块 `LIMIT 500`，于是「推爆配额 / 串行几十分钟」
    解决了，但**扫描本身仍是全表 + filesort** —— LIMIT 只有在有序索引上才能提前停下；
    没有 `updated_at` 索引时，MySQL 必须先扫全表再排序，LIMIT 只是丢弃尾部行。
    当时的台账状态因此写成「代码侧已完成，索引半边待迁移」。

    全站只有 `nodes.updated_at` 有索引（迁移 0035 为「节点列表默认排序」建的），
    其余 5 张被这个任务扫的表都没有。

覆盖的查询（逐条对过源码，都是 `updated_at >= 26 小时前` + 同列倒序 + LIMIT 500）：

| 表 | 服务的查询 | 已有索引为何不够 |
| --- | --- | --- |
| `tutorials` | 教程增量页 | `ix_tutorials_published_at` 首列是 `published_at`，且过滤列不同；`(is_pinned, is_recommended, *)` 前缀不匹配 |
| `tutorial_nodes` | 章节增量页 | 现有 3 个索引都是 `tutorial_id`/`parent_id`/`(tutorial_id, sort_order)` 前缀 |
| `plugins` | 扩展增量页 | 只有 `view_count`/`category`/`created_at` |
| `resources` | 资源增量页 | `ix_resources_pin_feat_download` 首列是 `is_pinned`（0020 那个含 updated_at 的复合索引未覆盖此查询的前缀） |
| `tools` | 工具增量页 | 只有 `is_published`/`access_mode`/`category` |

形态取舍：
    - 取**单列** `(updated_at)`。范围条件与排序同列，倒序索引扫描可直接在 500 行处停止；
      `tutorials` / `tools` 上还带一个 `is_published = 1` 的等值条件，但它是低基数标志位，
      命中 26 小时窗口的行本来就少，为其建复合索引的额外收益不及「多一处需要长期维护的形态」。
    - 索引名沿用 SQLAlchemy 默认 `ix_<表>_updated_at`，与模型里新加的 `index=True` **逐字一致**，
      否则 autogenerate 会反向 DROP（同 0035 / 0043 的处理）。
    - 这 5 张表都是**读多写极少**（内容由后台编辑，不是用户高频写入），索引的写放大可忽略。

幂等：与 0038~0043 同口径，每步都先查 `information_schema`，中断后可原地重跑。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_updated_at_indexes'
down_revision: Union[str, None] = 'add_plugin_view_count_index'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (索引名, 表名)；列恒为 updated_at，索引名按 SQLAlchemy 默认式派生
_TABLES = ("tutorials", "tutorial_nodes", "plugins", "resources", "tools")

# 这些表的 updated_at 索引**由更早的迁移建好了**，本迁移只跳过、不拥有，因此 downgrade 也不能删：
#   - resources.updated_at ← 20260908_0020_add_resource_sort_index（ix_resources_updated_at）
# 若在这里删掉，0020 的 downgrade 会再删一次 → 1091。
_OWNED_BY_OTHER = {"resources"}


def _offline() -> bool:
    """`alembic upgrade --sql`（离线生成 SQL）模式下拿不到可查询的连接。

    ⚠️ 同仓 0028 起的每条幂等守卫都直接 `op.get_bind().execute(...)`，
    于是**整个 `--sql` 路径从那时起就是坏的**（AttributeError: 'NoneType' has no
    attribute 'first'）。本文件先判离线模式，至少让新迁移不加重这个债；
    既有 9 个文件未回改（它们今天只在 online 模式下跑：CI 的 migrations job 与
    `scripts/verify_migrations.py` 都连真实 MySQL），已记进台账。
    """
    return bool(op.get_context().as_sql)


def _index_exists(table: str, index: str) -> bool:
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i LIMIT 1"
        ),
        {"t": table, "i": index},
    ).first()
    return row is not None


def upgrade() -> None:
    if _offline():
        # 离线模式无从判断存在性，直接产出建索引语句（与「无守卫」时代的语义一致）
        for table in _TABLES:
            op.create_index(f"ix_{table}_updated_at", table, ["updated_at"])
        return
    for table in _TABLES:
        name = f"ix_{table}_updated_at"
        if _index_exists(table, name):
            print(f"[add_updated_at_indexes] 跳过已存在的 {name}")
            continue
        op.create_index(name, table, ["updated_at"])
        print(f"[add_updated_at_indexes] 已建 {name} (updated_at)")


def downgrade() -> None:
    if _offline():
        for table in reversed(_TABLES):
            op.drop_index(f"ix_{table}_updated_at", table_name=table)
        return
    # 逆序删除，与 upgrade 严格镜像（5 张表互不依赖，逆序只为对称可读）
    for table in reversed(_TABLES):
        name = f"ix_{table}_updated_at"
        # ⚠️ `resources.updated_at` 的索引**不是本迁移建的**（0020 已建，upgrade 时被
        # `_index_exists` 跳过），所以 downgrade 也**绝不能删**它 ——
        # 否则 0020 的 downgrade 会再删一次，报 1091「Can't DROP ... check that column/key exists」。
        # 判据只能写死：downgrade 时无法区分「我建的」与「本来就有」，
        # 而「本来就有」的名单由更早的迁移静态决定（见 _OWNED_BY_OTHER）。
        if table in _OWNED_BY_OTHER:
            continue
        if _index_exists(table, name):
            op.drop_index(name, table_name=table)
