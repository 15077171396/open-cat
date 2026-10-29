"""知识百科（nodes）/ 插件（plugins）中文全文检索索引（台账 P1-17）

Revision ID: add_node_plugin_fulltext
Revises: add_node_plugin_seo_fields
Create Date: 2026-10-09

背景：
    台账 P1-1 用 MySQL 8 内置 ngram 全文索引替换了教程/资源的 LIKE 检索，
    但 ``services/search.py`` 当时只定义了 ``TUTORIAL_SEARCH_COLS`` 与
    ``RESOURCE_SEARCH_COLS``——即**站内搜索搜不到节点与插件**，而这两个模块
    正是「ComfyUI 节点大全」「ComfyUI 插件下载」这类栏目词的目标页。

本迁移与 0028 同构（同样用原生 SQL + 能力探测 + 幂等），刻意**自包含**而不复用
0028 的辅助函数：迁移是历史快照，应当与当时的应用代码解耦——若共用模块，
将来重构 ``app/`` 下任何东西都可能让一条已上线的老迁移跑不起来。

⚠️ 列**顺序**必须与 ``app/services/search.py`` 的
``NODE_SEARCH_COLS`` / ``PLUGIN_SEARCH_COLS`` 完全一致，否则 MATCH() 报
errno 1191（`tests/test_search.py` 会交叉校验两处并把它变成会失败的断言）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_node_plugin_fulltext'
down_revision: Union[str, None] = 'add_node_plugin_seo_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (表名, 索引名, 索引列) —— 列顺序必须与 app/services/search.py 一致
_INDEXES = (
    ("nodes", "ft_nodes_search", ("name", "description", "content")),
    ("plugins", "ft_plugins_search", ("name", "description", "content")),
)


def _server_version(conn) -> str:
    try:
        return str(conn.execute(sa.text("SELECT VERSION()")).scalar() or "")
    except Exception:  # pragma: no cover - 取不到版本时按「不支持」处理，走降级
        return ""


def _ngram_supported(conn) -> bool:
    """判断当前服务端是否支持 ``WITH PARSER ngram``（判定不了即按不支持处理）。

    MariaDB 有自有全文实现、没有 MySQL 的 ngram parser；MySQL 也要 5.7.6+ 才引入。
    跳过索引只是失去增强（检索自动退回 LIKE），硬建失败会让整条迁移炸掉、阻塞发版，
    两者代价不对等。
    """
    version = _server_version(conn).lower()
    if not version:
        return False
    if "mariadb" in version or "tidb" in version:
        return False
    try:
        parts = version.split(".")[:2]
        major, minor = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return False
    if (major, minor) < (5, 7):
        return False
    if (major, minor) == (5, 7):
        patch = 0
        try:
            patch = int(version.split(".")[2].split("-")[0])
        except (ValueError, IndexError):
            patch = 0
        if patch < 6:
            return False
    return True


def _index_exists(conn, table: str, index: str) -> bool:
    row = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
        ).bindparams(t=table, i=index)
    ).scalar()
    return bool(row)


def upgrade() -> None:
    conn = op.get_bind()

    if not _ngram_supported(conn):
        print(
            "\n[WARN] 当前数据库不支持 MySQL ngram parser（版本：{}），"
            "跳过节点/插件全文索引创建。\n"
            "       知识百科与插件检索将自动使用 LIKE 兜底，功能正常，"
            "仅失去分词带来的额外召回。\n"
            "       如需全文检索，请使用 MySQL 5.7.6+ / 8.x。\n".format(
                _server_version(conn) or "未知"
            )
        )
        return

    for table, index, cols in _INDEXES:
        if _index_exists(conn, table, index):
            continue
        col_sql = ", ".join(f"`{c}`" for c in cols)
        try:
            conn.execute(
                sa.text(
                    f"ALTER TABLE `{table}` "
                    f"ADD FULLTEXT INDEX `{index}` ({col_sql}) WITH PARSER ngram"
                )
            )
        except Exception as exc:  # 建索引失败不应阻塞发版，检索仍有 LIKE 兜底
            print(
                f"\n[WARN] 在 `{table}` 上创建全文索引失败，已跳过：{exc}\n"
                f"       检索功能不受影响（自动退回 LIKE），请排查后重跑本迁移。\n"
            )


def downgrade() -> None:
    conn = op.get_bind()
    for table, index, _cols in _INDEXES:
        if not _index_exists(conn, table, index):
            continue
        conn.execute(sa.text(f"ALTER TABLE `{table}` DROP INDEX `{index}`"))
