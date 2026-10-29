"""教程/资源中文全文检索索引（台账 P1-1）

Revision ID: add_fulltext_search
Revises: auth_request_pending_unique
Create Date: 2026-10-08

为什么需要这条迁移：
    教程与资源的关键词检索此前只有 ``LIKE '%kw%'``，中文**没有分词**——
    「人工智能教程」这类词只能靠整串子串匹配命中，内容量到数百篇后
    既慢又不准。这里改用 MySQL 8 内置的 **ngram 全文索引**：零新增组件
    （不用 Elasticsearch，对单机 2 核 4G 是重资产），中文按 2 字切分。

⚠️ 为什么用原生 SQL 而不是 op.create_index：
    Alembic/SQLAlchemy 的 ``create_index`` **不支持** ``WITH PARSER ngram``
    这个 MySQL 专属子句，只能 ``op.execute`` 手写 DDL。

⚠️ 为什么索引**不写进模型** ``__table_args__``：
    FULLTEXT 是 MySQL 专属特性，写进模型会让 ``alembic check`` 在不同方言下
    产生无意义的漂移噪音，且 autogenerate 无法表达 ``WITH PARSER``。
    索引只由本迁移维护，模型侧保持干净。

安全性设计（本迁移**不会**让部署失败）：
    1. 建索引前先做**能力探测**：MariaDB / MySQL < 5.7.6 不支持 ngram parser，
       此时打印醒目告警并**跳过**，检索自动退回 LIKE（见 search.py 的兜底设计），
       功能不受影响，只是失去分词带来的额外召回；
    2. **幂等**：索引已存在则跳过，避免重复执行或半途失败后重跑报错；
    3. 单条 ALTER 失败不会中断整条迁移，同样降级为告警。

运维提示：
    大表上新建 FULLTEXT 索引会重建索引，建议在低峰期执行；
    ``ngram_token_size`` 是**只读启动参数**，docker-compose 的 mysql 服务已加
    ``--ngram_token_size=2``，必须与 ``settings.SEARCH_NGRAM_TOKEN_SIZE`` 一致。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_fulltext_search'
down_revision: Union[str, None] = 'auth_request_pending_unique'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (表名, 索引名, 索引列) —— 列**顺序**必须与 app/services/search.py 里的
# TUTORIAL_SEARCH_COLS / RESOURCE_SEARCH_COLS 完全一致，否则 MATCH() 会报
# errno 1191 "Can't find FULLTEXT index matching the column list"。
_INDEXES = (
    ("tutorials", "ft_tutorials_search", ("title", "description", "content")),
    ("resources", "ft_resources_search", ("name", "description", "content")),
)


def _server_version(conn) -> str:
    try:
        return str(conn.execute(sa.text("SELECT VERSION()")).scalar() or "")
    except Exception:  # pragma: no cover - 取不到版本时按「不支持」处理，走降级
        return ""


def _ngram_supported(conn) -> bool:
    """判断当前服务端是否支持 ``WITH PARSER ngram``。

    MariaDB 有自有全文实现、没有 MySQL 的 ngram parser；MySQL 也要 5.7.6+
    才引入 ngram。判定不了就按不支持处理——跳过索引只是失去增强，
    硬建失败会让整条迁移炸掉、阻塞发版，代价不对等。
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
        # 5.7 的 ngram parser 自 5.7.6 起提供
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
            "跳过全文索引创建。\n"
            "       教程/资源检索将自动使用 LIKE 兜底，功能正常，"
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
