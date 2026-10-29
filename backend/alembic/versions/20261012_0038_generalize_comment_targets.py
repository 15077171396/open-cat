"""评论泛化为「任意内容类型」：tutorial_comments → content_comments

Revision ID: generalize_comment_targets
Revises: add_audit_source_fields
Create Date: 2026-10-12

背景（用户需求）：
    百科节点详情页要加评论区（与教程详情页同构）。原表只有 ``tutorial_id``，
    若沿用「再加一列 node_id」的做法，每新增一种可评论的内容就要多一列、
    并在 service / API / 管理端各加一处分支 —— 而「新增一种内容类型要改 N 处
    硬编码」正是本项目审查里最弱的一环（易扩展）。

    故改为多态目标 ``(target_type, target_id)``，并同步把表名与索引名从
    ``*_tutorial_*`` 换成中性的 ``content_*`` / ``ix_cc_*``（否则以后会有人问
    「为什么节点评论存在 tutorial_comments 表里」）。

数据迁移（本迁移的**唯一**有损步骤是 downgrade，见下）：
    - upgrade：新增两列（先带默认值保证非空）→ **分批**回填 ``target_id = tutorial_id``
      → 建新复合索引 → 下线旧索引与旧列 → 改名 → 去掉临时默认值。
      回填后 ``target_type`` 一律为 'tutorial'，与历史语义一致，**零数据丢失**。
    - downgrade：旧表结构表达不了非教程评论，因此会**删除** ``target_type <> 'tutorial'``
      的行，并在删除前打印数量与类型分布（不静默降级）。这是有意为之：与其把节点评论
      静默写成 tutorial_id，不如让回滚明确地丢掉这部分数据。

幂等性（第五轮审查 R5-09 的整改）：
    MySQL 的 DDL **不是**事务性的，而本迁移有 10+ 步 ALTER/UPDATE。原实现每步都假定
    「自己是第一次跑」：一旦在中途被杀（连接断、容器 OOM、磁盘满），``alembic_version``
    还没推进，原地重跑就会在第一个 ``add_column`` 撞 ``Duplicate column name``，
    把一个「重跑即可恢复」的失败变成需要手工拼 SQL 的故障。
    现在每一步都先探测现状（沿用本仓 `2026_07_22_0001` / `0028` / `0032` 的写法），
    因此**中断后原地再跑一次 ``alembic upgrade head`` 就能续上**。

    ⚠️ 回填改为按主键分批（原来是一条无 LIMIT 的全表 UPDATE）：单语句会把每一行都加上
    排他锁并写等量 undo/binlog，在单主机上与站点读写抢同一池连接。

⚠️ 本文件改过之后需要重跑一次 ``scripts/verify_migrations.py``（空库 + 有数据两种），
   它会连真实 MySQL 建/删临时库，属运维动作，由人工执行。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'generalize_comment_targets'
down_revision: Union[str, None] = 'add_audit_source_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 列宽刻意硬编码（迁移必须是历史快照，不随 app 常量漂移）
TARGET_TYPE_LENGTH = 20

OLD_TABLE = "tutorial_comments"
NEW_TABLE = "content_comments"

# 仅改名的索引：(upgrade 后的新名, 旧名)
RENAMED_INDEXES = (
    ("ix_cc_user_created", "ix_tc_user_created"),
    ("ix_cc_deleted_at", "ix_tc_deleted_at"),
    ("ix_cc_parent", "ix_tc_parent"),
)

OLD_COMPOSITE_INDEX = "ix_tc_tutorial_deleted_created"
NEW_COMPOSITE_INDEX = "ix_cc_target_deleted_created"

# 回填批次：够大以免小库反复往返，够小以免一条语句锁完整张表
BACKFILL_BATCH = 5000


def _conn():
    return op.get_bind()


def _table_exists(table: str) -> bool:
    row = _conn().execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": table},
    ).first()
    return row is not None


def _column_exists(table: str, column: str) -> bool:
    row = _conn().execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def _index_exists(table: str, index: str) -> bool:
    row = _conn().execute(
        sa.text(
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i "
            "LIMIT 1"
        ),
        {"t": table, "i": index},
    ).first()
    return row is not None


def _has_server_default(table: str, column: str) -> bool:
    """该列当前**是否**带默认值（决定第 6 步要不要执行）"""
    row = _conn().execute(
        sa.text(
            "SELECT COLUMN_DEFAULT FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None and row[0] is not None


def _backfill_batched(table: str, target_col: str, source_col: str) -> int:
    """按主键分批回填，返回累计更新行数。

    ``WHERE {target_col} = 0`` 让每批只碰「还没回填」的行，因此本函数**可重入**；
    真实 id 由 ``generate_id()`` 生成且非 0，0 只可能是临时默认值留下的。
    """
    total = 0
    while True:
        result = _conn().execute(
            sa.text(
                f"UPDATE {table} SET {target_col} = {source_col} "
                f"WHERE {target_col} = 0 ORDER BY id LIMIT :n"
            ),
            {"n": BACKFILL_BATCH},
        )
        n = result.rowcount or 0
        total += n
        if n < BACKFILL_BATCH:
            return total


def upgrade() -> None:
    # 中断重跑时表可能已经改过名，两步都要认
    if _table_exists(OLD_TABLE):
        table = OLD_TABLE
    elif _table_exists(NEW_TABLE):
        table = NEW_TABLE
    else:
        raise RuntimeError(
            f"[generalize_comment_targets] 既找不到 {OLD_TABLE} 也找不到 {NEW_TABLE} —— "
            "评论表不存在，请确认 add_audit_source_fields 之前的建表迁移是否跑过"
        )

    # 1) 先加列并给默认值：已有行必须立刻满足 NOT NULL
    if not _column_exists(table, "target_type"):
        op.add_column(
            table,
            sa.Column(
                "target_type",
                sa.String(TARGET_TYPE_LENGTH),
                nullable=False,
                server_default="tutorial",
                comment="目标类型(tutorial/node/…)",
            ),
        )
    if not _column_exists(table, "target_id"):
        op.add_column(
            table,
            sa.Column(
                "target_id",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
                comment="目标内容ID",
            ),
        )

    # 2) 分批回填：历史数据全部是教程评论
    if _column_exists(table, "tutorial_id"):
        moved = _backfill_batched(table, "target_id", "tutorial_id")
        print(f"[generalize_comment_targets] 回填 target_id ← tutorial_id：{moved} 行（每批 {BACKFILL_BATCH}）")

    # 3) 新复合索引（覆盖「某内容下、未删除、按时间倒序」）
    if not _index_exists(table, NEW_COMPOSITE_INDEX):
        op.create_index(
            NEW_COMPOSITE_INDEX,
            table,
            ["target_type", "target_id", "deleted_at", "created_at"],
        )

    # 4) 旧索引必须先于旧列下线（索引唯一列被删时 MySQL 会报错）
    if _index_exists(table, OLD_COMPOSITE_INDEX):
        op.drop_index(OLD_COMPOSITE_INDEX, table_name=table)
    if _column_exists(table, "tutorial_id"):
        op.drop_column(table, "tutorial_id")

    # 5) 索引与表改名（MySQL 8 均为元数据操作，原子且瞬时）
    for new_name, old_name in RENAMED_INDEXES:
        if _index_exists(table, old_name) and not _index_exists(table, new_name):
            op.execute(f"ALTER TABLE {table} RENAME INDEX {old_name} TO {new_name}")
    if table == OLD_TABLE:
        op.rename_table(OLD_TABLE, NEW_TABLE)
    table = NEW_TABLE

    # 6) 去掉临时默认值：目标必须显式指定，漏写应报错而不是静默落成教程评论
    for col, coltype in (("target_type", sa.String(TARGET_TYPE_LENGTH)), ("target_id", sa.BigInteger())):
        if _has_server_default(table, col):
            op.alter_column(table, col, existing_type=coltype, nullable=False, server_default=None)


def downgrade() -> None:
    # 与 upgrade 严格对称、逆序执行
    if _table_exists(NEW_TABLE):
        op.rename_table(NEW_TABLE, OLD_TABLE)
    for new_name, old_name in RENAMED_INDEXES:
        if _index_exists(OLD_TABLE, new_name) and not _index_exists(OLD_TABLE, old_name):
            op.execute(f"ALTER TABLE {OLD_TABLE} RENAME INDEX {new_name} TO {old_name}")

    # 旧结构只有 tutorial_id：非教程评论无法表达，显式删除（不做静默降级）。
    # 删除前先数清楚「丢了多少、都是谁的」——回滚后没人能凭记忆回答这个问题。
    rows = _conn().execute(
        sa.text(f"SELECT target_type, COUNT(*) FROM {OLD_TABLE} WHERE target_type <> 'tutorial' GROUP BY target_type")
    ).all()
    doomed = sum(n for _t, n in rows)
    if doomed:
        dist = ", ".join(f"{t}={n}" for t, n in rows)
        print(
            f"[generalize_comment_targets] downgrade 将**删除** {doomed} 条非教程评论（{dist}）；"
            "旧表结构无法表达它们。需要保留请先自行导出。"
        )
        _conn().execute(sa.text(f"DELETE FROM {OLD_TABLE} WHERE target_type <> 'tutorial'"))

    if not _column_exists(OLD_TABLE, "tutorial_id"):
        op.add_column(
            OLD_TABLE,
            sa.Column("tutorial_id", sa.BigInteger(), nullable=False, server_default="0"),
        )
    if _column_exists(OLD_TABLE, "target_id"):
        moved = _backfill_batched(OLD_TABLE, "tutorial_id", "target_id")
        print(f"[generalize_comment_targets] downgrade 回填 tutorial_id ← target_id：{moved} 行")

    if not _index_exists(OLD_TABLE, OLD_COMPOSITE_INDEX):
        op.create_index(
            OLD_COMPOSITE_INDEX,
            OLD_TABLE,
            ["tutorial_id", "deleted_at", "created_at"],
        )
    if _index_exists(OLD_TABLE, NEW_COMPOSITE_INDEX):
        op.drop_index(NEW_COMPOSITE_INDEX, table_name=OLD_TABLE)

    if _has_server_default(OLD_TABLE, "tutorial_id"):
        op.alter_column(
            OLD_TABLE, "tutorial_id",
            existing_type=sa.BigInteger(), nullable=False, server_default=None,
        )
    for col in ("target_type", "target_id"):
        if _column_exists(OLD_TABLE, col):
            op.drop_column(OLD_TABLE, col)
