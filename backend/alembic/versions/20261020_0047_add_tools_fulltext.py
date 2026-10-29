"""工具（tools）中文全文检索索引 —— 补上 0028/0032 漏掉的第 5 张表

Revision ID: add_tools_fulltext
Revises: add_content_list_indexes
Create Date: 2026-10-20

为什么现在补（第二轮复审 C-01，一个「静默且不可逆」的功能缺陷）：
    `services/search.py` 定义了 5 组检索列（tutorial / resource / node / plugin / tool），
    但全文索引只建了 4 张表：0028 建 tutorials / resources，0032 建 nodes / plugins。
    `tools` 一直没有 —— 而 `TOOL_SEARCH_COLS` 仍然会构造 MATCH() 候选，于是：

        搜一次工具（≥2 字） → MySQL 报 errno 1191
        → `mark_fulltext_unavailable()`（当时是**进程级**开关）
        → 教程 / 资源 / 百科 / 扩展的全文检索**一起**退回 LIKE 全表扫描，直到重启。

    也就是说：一个「某张表少了个索引」的问题，被放大成了「全站搜索质量永久下降」。
    修法是两层：本迁移补上索引（根因），`search.py` 把熔断粒度改成按列组（防扩散）。
    两层都做是刻意的 —— 迁移在「MySQL 不支持 ngram」时会跳过建索引，那时按列组熔断
    能保证只有 tools 退化为 LIKE，不会拖累其它 4 张表。

⚠️ 列**顺序**必须与 ``app/services/search.py`` 的 ``TOOL_SEARCH_COLS`` 完全一致
（`name`, `description`），否则 MATCH() 依旧报 errno 1191。
`tests/test_search_fulltext_coverage.py` 会交叉校验「每个 *_SEARCH_COLS 都有对应索引」。

与 0028/0032 同构（原生 SQL + 能力探测 + 幂等 + 失败不阻塞发版），并刻意**自包含**：
迁移是历史快照，不应依赖 app/ 下可能被重构的辅助函数。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_tools_fulltext'
down_revision: Union[str, None] = 'add_content_list_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (表名, 索引名, 索引列) —— 列顺序必须与 app/services/search.py 一致
_INDEXES = (
    ("tools", "ft_tools_search", ("name", "description")),
)


def _server_version(conn) -> str:
    try:
        return str(conn.execute(sa.text("SELECT VERSION()")).scalar() or "")
    except Exception:  # pragma: no cover - 取不到版本时按「不支持」处理，走降级
        return ""


def _ngram_supported(conn) -> bool:
    """判断当前服务端是否支持 ``WITH PARSER ngram``（与 0028 同口径）。"""
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
            "跳过工具全文索引创建。\n"
            "       工具检索将自动使用 LIKE 兜底（仅这一张表），功能正常，"
            "其它内容类型的全文检索不受影响。\n"
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
                f"       工具检索将退回 LIKE，其它内容类型不受影响，请排查后重跑本迁移。\n"
            )


def downgrade() -> None:
    conn = op.get_bind()
    for table, index, _cols in _INDEXES:
        if not _index_exists(conn, table, index):
            continue
        conn.execute(sa.text(f"ALTER TABLE `{table}` DROP INDEX `{index}`"))
