"""回填内容「发布作者」（一次性数据修复，2026-10-13）。

背景：
    `author_name` / `author_avatar_url` 上线时是让运营手填的，后改为**系统自动带出**
    （见 `app/services/content_author.py`）。新规则只对**新建**和**编辑过**的内容生效，
    历史内容仍是 NULL —— 而前台约定「作者为 NULL 就不渲染作者行」，于是卡片上看不到作者。

本脚本把历史内容补齐，取值顺序：
1. **审计日志**：`admin_audit_logs` 里 `content_create` 记录的创建者（最准确）；
2. **发布账号兜底**：没有创建记录时，用「创建过内容的最近一个管理员账号」的昵称（回落账号）。

用法：
    cd backend && venv/Scripts/python.exe ../scripts/backfill_content_author.py --dry-run
    cd backend && venv/Scripts/python.exe ../scripts/backfill_content_author.py

⚠️ 只回填 `author_name IS NULL` 的行 —— **不会覆盖已有值**（人工填过的、上游作者等一律保留）。
⚠️ 幂等：重复执行不会有额外影响。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # 让 pydantic-settings 读到 backend/.env

import pymysql  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

# (表名, 审计日志里的 target_type)
TARGETS = (("plugins", "plugin"), ("resources", "resource"), ("nodes", "node"))


def connect(autocommit: bool = True):
    env = dotenv_values(BACKEND / ".env")
    return pymysql.connect(
        host=env["DB_HOST"], port=int(env["DB_PORT"]), user=env["DB_USER"],
        password=env["DB_PASSWORD"], database=env["DB_NAME"], autocommit=autocommit,
    )


def resolve_fallback_author(cur) -> tuple[int | None, str | None, str | None]:
    """发布账号兜底：取「创建过内容的最近一个管理员」的昵称（回落账号）+ 头像"""
    cur.execute(
        """
        SELECT u.id,
               COALESCE(NULLIF(TRIM(COALESCE(u.nickname, '')), ''), u.account),
               u.avatar_url
        FROM admin_audit_logs a
        JOIN users u ON u.id = a.admin_id
        WHERE a.action = 'content_create' AND a.target_type IN ('plugin', 'resource', 'node')
        ORDER BY a.created_at DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    return (row[0], row[1], row[2]) if row else (None, None, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument(
        "--alias",
        action="append",
        default=[],
        metavar="旧昵称",
        help=(
            "发布账号用过的历史昵称（可重复传）。内容行里的 author_name 是**发布当时的快照**，"
            "账号改过昵称后就对不上当前昵称了；把这些旧名告诉本脚本，才能把历史内容链接回账号。"
        ),
    )
    args = ap.parse_args()

    # --dry-run 时关闭 autocommit，最后 rollback，保证一行都不落库
    conn = connect(autocommit=not args.dry_run)
    with conn.cursor() as cur:
        fb_id, fb_name, fb_avatar = resolve_fallback_author(cur)
        if not fb_name:
            print("✗ 审计日志里没有任何 content_create 记录，无法推断发布账号；已退出")
            return 1
        print(f"发布账号兜底值: author_name={fb_name!r} author_avatar_url={fb_avatar!r}")
        print()

        total_from_audit = total_from_fallback = 0
        for table, target_type in TARGETS:
            # ① 审计日志回填（最准确）
            cur.execute(
                f"""
                UPDATE {table} x
                JOIN admin_audit_logs a
                  ON a.target_type = %s AND a.target_id = x.id AND a.action = 'content_create'
                LEFT JOIN users u ON u.id = a.admin_id
                SET x.author_name = COALESCE(NULLIF(TRIM(COALESCE(u.nickname, '')), ''), u.account),
                    x.author_avatar_url = COALESCE(u.avatar_url, x.author_avatar_url)
                WHERE x.author_name IS NULL
                """,
                (target_type,),
            )
            n_audit = cur.rowcount
            # ② 兜底：剩下的空值统一归属发布账号
            cur.execute(
                f"""
                UPDATE {table}
                SET author_name = %s, author_avatar_url = COALESCE(author_avatar_url, %s)
                WHERE author_name IS NULL
                """,
                (fb_name, fb_avatar),
            )
            n_fb = cur.rowcount
            total_from_audit += n_audit
            total_from_fallback += n_fb
            cur.execute(f"SELECT COUNT(*), SUM(author_name IS NOT NULL) FROM {table}")
            tot, filled = cur.fetchone()
            print(f"  {table:10s} 审计回填 {n_audit} 行 + 兜底 {n_fb} 行 → 已有作者 {filled}/{tot}")

        print()
        print(f"合计：审计回填 {total_from_audit} 行，兜底 {total_from_fallback} 行")

        # ③ author_id 回填（2026-10-13 新增）：内容新增 author_id 引用后，
        #    只有「审计日志能证明是谁创建的」与「author_name 能唯一对上某个账号」两种
        #    情况才写 id —— 第三方导入带出的原文作者（如 GitHub owner）在站内没有账号，
        #    必须保持 NULL，否则展示会被绑到一个不相干的人身上。
        print()
        print("author_id 回填：")
        total_ids = 0
        for table, target_type in TARGETS:
            # a) 审计日志：创建记录
            cur.execute(
                f"""
                UPDATE {table} x
                JOIN admin_audit_logs a
                  ON a.target_type = %s AND a.target_id = x.id AND a.action = 'content_create'
                SET x.author_id = a.admin_id
                WHERE x.author_id IS NULL
                """,
                (target_type,),
            )
            n_audit_id = cur.rowcount
            # b0) 按「发布账号的历史昵称」匹配（快照存的是当时的昵称，改过名就对不上当前值）
            if args.alias:
                placeholders = ", ".join(["%s"] * len(args.alias))
                cur.execute(
                    f"""
                    UPDATE {table}
                    SET author_id = %s
                    WHERE author_id IS NULL AND author_name IN ({placeholders})
                    """,
                    (fb_id, *args.alias),
                )
                n_alias_id = cur.rowcount
                if n_alias_id:
                    print(f"  {table:10s} 历史昵称匹配 {n_alias_id} 行")
                total_ids += n_alias_id

            # b) 按作者名唯一匹配账号（昵称或账号完全相同，且只有一个用户命中）
            cur.execute(
                f"""
                UPDATE {table} x
                JOIN (
                    SELECT u.id,
                           COALESCE(NULLIF(TRIM(COALESCE(u.nickname, '')), ''), u.account) AS display_name,
                           u.account
                    FROM users u
                ) d ON d.display_name = x.author_name OR d.account = x.author_name
                SET x.author_id = d.id
                WHERE x.author_id IS NULL
                  AND (SELECT COUNT(*) FROM users u2
                       WHERE COALESCE(NULLIF(TRIM(COALESCE(u2.nickname, '')), ''), u2.account) = x.author_name
                          OR u2.account = x.author_name) = 1
                """
            )
            n_name_id = cur.rowcount
            total_ids += n_audit_id + n_name_id
            cur.execute(f"SELECT COUNT(*), SUM(author_id IS NOT NULL) FROM {table}")
            tot, filled = cur.fetchone()
            print(f"  {table:10s} 审计 {n_audit_id} 行 + 名字唯一匹配 {n_name_id} 行 → 已有 author_id {filled}/{tot}")
        print(f"合计回填 author_id {total_ids} 行")

        if args.dry_run:
            print("（--dry-run 仅统计，未写库）")
            conn.rollback()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
