#!/usr/bin/env python3
"""孤儿数据清理 SQL 生成器（零依赖）：为收藏/学习记录表的存量孤儿行生成统计与清理 SQL。

只生成 SQL 而不直接连库执行：删除不可逆，先统计确认数量再人工执行；
新增内容的孤儿已由代码删除路径同事务清理，本脚本只处理历史遗留的存量孤儿。

用法：python scripts/cleanup_orphans.py [--out PATH] [--counts-only]
退出码：恒为 0（本脚本不连接数据库，无法自查）
"""

from __future__ import annotations

import argparse
import sys

# (收藏目标类型, 对应的实体表)
FAVORITE_TARGETS = (
    ("tutorial", "tutorials"),
    ("resource", "resources"),
    ("plugin", "plugins"),
    ("node", "nodes"),
)

# 学习记录只指向教程
LEARNING_TARGET = ("tutorial_id", "tutorials")

COMMENT = """-- ============================================================
-- 孤儿数据清理（缺口台账 P1-7）
-- 生成时间：由 scripts/cleanup_orphans.py 生成，执行前请人工复核
--
-- 说明：收藏与学习记录存的是快照、无外键，内容硬删除后不会级联，
--       残留行在用户侧就是「我的收藏 / 我的学习」里点开即 404 的死条目。
--       新增内容的孤儿已由代码修复（删除时同事务清理引用），
--       本脚本只清理**历史遗留**的存量孤儿。
--
-- ⚠️ 执行顺序：先跑【统计】确认数量符合预期，再跑【清理】。
-- ⚠️ 建议先备份：./scripts/backup_mysql.sh
-- ============================================================
"""


def count_statements() -> list[str]:
    """统计各类孤儿数量的 SELECT 语句"""
    stmts = ["-- ---------- 【统计】各类孤儿数量 ----------"]
    for target_type, table in FAVORITE_TARGETS:
        stmts.append(
            f"SELECT 'favorites/{target_type}' AS kind, COUNT(*) AS orphans "
            f"FROM favorites f "
            f"WHERE f.target_type = '{target_type}' "
            f"AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = f.target_id);"
        )
    col, table = LEARNING_TARGET
    stmts.append(
        f"SELECT 'learning_records/{col}' AS kind, COUNT(*) AS orphans "
        f"FROM learning_records l "
        f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = l.{col});"
    )
    return stmts


def delete_statements() -> list[str]:
    """清理孤儿行的 DELETE 语句（用 NOT EXISTS 而非 NOT IN：目标 id 含 NULL 时 NOT IN 会整体返回空集）"""
    stmts = ["-- ---------- 【清理】删除孤儿行 ----------"]
    for target_type, table in FAVORITE_TARGETS:
        stmts.append(
            f"DELETE FROM favorites "
            f"WHERE target_type = '{target_type}' "
            f"AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = favorites.target_id);"
        )
    col, table = LEARNING_TARGET
    stmts.append(
        f"DELETE FROM learning_records "
        f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = learning_records.{col});"
    )
    return stmts


def build_sql(*, counts_only: bool = False) -> str:
    blocks = [COMMENT]
    blocks.append("\n".join(count_statements()))
    if not counts_only:
        blocks.append("\n".join(delete_statements()))
    return "\n\n".join(blocks) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成孤儿数据（收藏 / 学习记录）的统计与清理 SQL（P1-7）",
    )
    parser.add_argument("--out", metavar="PATH", help="写入文件而非打印到标准输出")
    parser.add_argument("--counts-only", action="store_true", help="只生成统计语句")
    args = parser.parse_args(argv)

    sql = build_sql(counts_only=args.counts_only)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(sql)
        print(f"已写入 {args.out}（{len(sql.splitlines())} 行）")
        print("执行前请先备份：./scripts/backup_mysql.sh")
    else:
        sys.stdout.write(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
