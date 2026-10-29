"""评论回复的往返次数与读量上界（缺口台账 P2-44）。

`list_comments` 要为「本页每条顶层评论」带出前 N 条回复。历史上两个版本都有问题：

- **一次读全**（P1-43① 之前）：一条热评上千回复时，每次翻页读回上千行再丢掉 99%；
- **逐父一条查询**（P1-43①）：读量压住了，但共享 session 不能并发 →
  最多 `page_size`（20）条**串行**往返，一次翻页 ≈24 次 MySQL RTT。

现在的做法是「按回复数分流」：≤ N 的父用**一条 IN 查询**全取（读量天然有界 =
父数 × N），只有热评（> N）才各自一条带 LIMIT 的查询。于是语句数从
「随 page_size 线性增长」变成「5 + 热评数」，而热评在真实数据里是极少数。

本文件钉的就是这两条：**语句数不随父评论数增长**、**读量与截断语义不变**。
（仿真层此前会静默忽略 ORDER BY/LIMIT，那些断言在这里才有意义 —— 见 P2-51。）
"""

import datetime as dt
import unittest
from unittest import mock

from sqlalchemy import select

from app.models.comment import MAX_REPLIES_PER_COMMENT, ContentComment
from app.models.tutorial import Tutorial
from app.models.user import User
from app.services import comment_service
from tests.fake_db import MemorySession

TID = 100
BASE_TIME = dt.datetime(2026, 1, 1, 0, 0, 0)


class _CountingSession(MemorySession):
    """记录每一条被执行的 SELECT，用于断言往返次数。"""

    def __init__(self):
        super().__init__()
        self.sql: list[str] = []

    async def execute(self, stmt, *args, **kwargs):
        try:
            self.sql.append(str(stmt))
        except Exception:  # pragma: no cover - 防御
            self.sql.append(repr(stmt))
        return await super().execute(stmt, *args, **kwargs)

    def reply_selects(self) -> list[str]:
        """只数「取回复」的语句。

        注意区分三条都带 `parent_id` 字样的语句：顶层分页用的是 `parent_id IS NULL`、
        计数那条是 GROUP BY，两者都不算取回复。
        """
        out = []
        for s in self.sql:
            upper = s.upper()
            if "parent_id is null" in s.lower():
                continue
            if "parent_id" not in s or "GROUP BY" in upper or "count(" in s.lower():
                continue
            out.append(s)
        return out


def _seed_parents_with_replies(db: MemorySession, parents: dict[int, int], user_id: int = 7) -> None:
    """parents = {父评论 id: 回复数}；回复 id 从 9000+父id 起，created_at 递增。"""
    db.seed(User(id=user_id, account=f"u{user_id}@example.com", password_hash="x", role="user"))
    tutorial = Tutorial(id=TID, title="教程", description="简介", content="正文", category="ai")
    tutorial.is_published = True
    db.seed(tutorial)

    seq = 0
    for pid, n in parents.items():
        db.seed(ContentComment(
            id=pid, target_type="tutorial", target_id=TID, user_id=user_id, parent_id=None,
            content=f"顶层 {pid}", created_at=BASE_TIME + dt.timedelta(minutes=pid),
        ))
        for i in range(n):
            seq += 1
            db.seed(ContentComment(
                id=9000 + seq, target_type="tutorial", target_id=TID, user_id=user_id, parent_id=pid,
                content=f"回复 {pid}-{i}", created_at=BASE_TIME + dt.timedelta(minutes=pid, seconds=i),
            ))


class ReplyRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_statement_count_does_not_grow_with_parent_count(self):
        """核心不变量：没有热评时，取回复只用一条 IN 查询，且不随父评论数量增长。"""
        counts = {}
        for n_parents in (2, 8, 20):
            db = _CountingSession()
            _seed_parents_with_replies(db, {1000 + i: 3 for i in range(n_parents)})
            listing = await comment_service.list_comments(db, "tutorial", TID, page=1, page_size=20)
            self.assertEqual(len(listing["items"]), n_parents)
            counts[n_parents] = len(db.reply_selects())
        self.assertEqual(counts[2], 1, f"无热评时应只用一条 IN 查询，实际 {counts}")
        self.assertEqual(counts[2], counts[8], f"语句数随父数量增长：{counts}")
        self.assertEqual(counts[8], counts[20], f"语句数随父数量增长：{counts}")

    async def test_hot_comment_is_fetched_separately_and_truncated(self):
        """热评（回复数 > N）单独一条查询，且只回前 N 条、reply_count 仍是真实总数。"""
        cap = 3
        db = _CountingSession()
        _seed_parents_with_replies(db, {1001: cap + 4, 1002: 2})
        with mock.patch.object(comment_service, "MAX_REPLIES_PER_COMMENT", cap):
            listing = await comment_service.list_comments(db, "tutorial", TID, page=1, page_size=20)

        by_id = {item["id"]: item for item in listing["items"]}
        self.assertEqual(len(by_id[1001]["replies"]), cap, "热评必须截断到展示上限")
        self.assertEqual(by_id[1001]["reply_count"], cap + 4, "reply_count 必须是真实总数")
        self.assertEqual(len(by_id[1002]["replies"]), 2)
        self.assertEqual(by_id[1002]["reply_count"], 2)
        # 一条 IN（只含小集合父）+ 一条热评查询
        self.assertEqual(len(db.reply_selects()), 2)
        # 截断必须取「最早」的 N 条，而不是随机 N 条
        contents = [r["content"] for r in by_id[1001]["replies"]]
        self.assertEqual(contents, [f"回复 1001-{i}" for i in range(cap)])

    async def test_read_volume_is_bounded_by_parents_times_cap(self):
        """读量上界：即使某父回复很多，一条 IN 查询也不会把该父全部读回。"""
        cap = 3
        db = _CountingSession()
        _seed_parents_with_replies(db, {1001: 50, 1002: 50, 1003: 1})
        with mock.patch.object(comment_service, "MAX_REPLIES_PER_COMMENT", cap):
            await comment_service.list_comments(db, "tutorial", TID, page=1, page_size=20)
        in_selects = [s for s in db.reply_selects() if "IN (" in s or "IN (" in s.upper()]
        for s in in_selects:
            # 小集合分支只应包含未超限的父（这里是 1003）；超限父走各自的 LIMIT 查询
            self.assertNotIn("1001", s.split("WHERE")[-1], "热评父评论不该进 IN 查询，否则读量失控")

    async def test_same_second_replies_ordered_by_id(self):
        """created_at 并列时靠 id 定序 —— 否则两次翻页可能取到不同的前 N 条。"""
        db = _CountingSession()
        tutorial = Tutorial(id=TID, title="教程", description="简介", content="正文", category="ai")
        tutorial.is_published = True
        db.seed(
            tutorial,
            User(id=7, account="u7@example.com", password_hash="x", role="user"),
            ContentComment(id=1001, target_type="tutorial", target_id=TID, user_id=7, parent_id=None,
                            content="顶层", created_at=BASE_TIME),
        )
        same = BASE_TIME + dt.timedelta(days=1)
        for rid in (5001, 5002, 5003):
            db.seed(ContentComment(id=rid, target_type="tutorial", target_id=TID, user_id=7, parent_id=1001,
                                    content=f"回复 {rid}", created_at=same))
        with mock.patch.object(comment_service, "MAX_REPLIES_PER_COMMENT", 2):
            listing = await comment_service.list_comments(db, "tutorial", TID, page=1, page_size=20)
        got = [r["id"] for r in listing["items"][0]["replies"]]
        self.assertEqual(got, [5001, 5002], f"并列时必须按 id 稳定截断，实际 {got}")


class ReplySemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_soft_deleted_replies_excluded_and_not_counted(self):
        db = _CountingSession()
        _seed_parents_with_replies(db, {1001: 4})
        deleted = [r for r in db._table(ContentComment).rows.values() if r["content"] == "回复 1001-1"]
        for row in deleted:
            row["deleted_at"] = BASE_TIME + dt.timedelta(days=2)

        listing = await comment_service.list_comments(db, "tutorial", TID, page=1, page_size=20)
        item = listing["items"][0]
        contents = [r["content"] for r in item["replies"]]
        self.assertNotIn("回复 1001-1", contents)
        self.assertEqual(item["reply_count"], 3, "软删回复不得计入真实总数")

    async def test_default_cap_allows_small_threads_in_one_query(self):
        """默认上限 50：常规讨论串全部走一条 IN 查询。"""
        self.assertGreater(MAX_REPLIES_PER_COMMENT, 10)
        db = _CountingSession()
        _seed_parents_with_replies(db, {1001: 6, 1002: 9})
        listing = await comment_service.list_comments(db, "tutorial", TID, page=1, page_size=20)
        by_id = {i["id"]: len(i["replies"]) for i in listing["items"]}
        self.assertEqual(by_id, {1001: 6, 1002: 9})
        self.assertEqual(len(db.reply_selects()), 1)


if __name__ == "__main__":
    unittest.main()
