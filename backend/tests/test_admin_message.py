"""管理端群发系统通知：接收范围、参数校验、批次聚合。

重点钉三件容易出错的事：
1. **「指定用户」里有一个不存在就必须整体失败** —— 选了 5 个人只发出 3 条，管理员不会发现；
2. **跳转链接只允许站内相对路径** —— 这个值会原样渲染成 `<Link to>`，写错一次就是站内跳转漏洞；
3. **批次聚合**：一次群发在库里是 N 行，后台列表必须按批次聚合成 1 条，并统计送达/已读。
"""

import unittest

from app.core.exceptions import AppException
from app.models.message import Message
from app.services import admin_message_service
from tests.fake_db import MemorySession
from tests.test_comments import make_user


class SendSystemMessageTests(unittest.IsolatedAsyncioTestCase):
    def _seed(self, count: int = 3) -> MemorySession:
        db = MemorySession()
        db.seed(*[make_user(i) for i in range(1, count + 1)])
        return db

    async def test_send_to_all_users(self):
        db = self._seed(3)
        result = await admin_message_service.send_system_message(
            db, admin_id=99, title="新专题上线", content="AI 绘画专题已更新", target="all"
        )
        self.assertEqual(result["sent_count"], 3)
        rows = db.all_rows(Message)
        self.assertEqual(len(rows), 3, "每个用户各一条")
        self.assertEqual({r["user_id"] for r in rows}, {1, 2, 3})
        self.assertEqual({r["batch_id"] for r in rows}, {result["batch_id"]}, "同批共用一个批次号")
        self.assertEqual({r["type"] for r in rows}, {"system"})
        self.assertTrue(all(r["sender_id"] is None for r in rows), "系统通知不暴露具体管理员")

    async def test_send_to_selected_users(self):
        db = self._seed(3)
        result = await admin_message_service.send_system_message(
            db, admin_id=99, title="定向通知", content="只发给 1 和 3", target="users", user_ids=[1, 3]
        )
        self.assertEqual(result["sent_count"], 2)
        self.assertEqual({r["user_id"] for r in db.all_rows(Message)}, {1, 3})

    async def test_unknown_user_aborts_whole_send(self):
        """部分用户不存在时整体失败：静默跳过会让管理员以为全都发到了。"""
        db = self._seed(2)
        with self.assertRaises(AppException) as ctx:
            await admin_message_service.send_system_message(
                db, admin_id=99, title="t", content="c", target="users", user_ids=[1, 999]
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("999", str(ctx.exception.message))
        self.assertEqual(db.all_rows(Message), [], "校验失败不得写入任何行")

    async def test_empty_selection_rejected(self):
        db = self._seed(2)
        with self.assertRaises(AppException):
            await admin_message_service.send_system_message(
                db, admin_id=99, title="t", content="c", target="users", user_ids=[]
            )

    async def test_blank_title_or_content_rejected(self):
        db = self._seed(2)
        for title, content in (("  ", "c"), ("t", "   ")):
            with self.assertRaises(AppException, msg=f"{title!r}/{content!r}"):
                await admin_message_service.send_system_message(
                    db, admin_id=99, title=title, content=content
                )
        self.assertEqual(db.all_rows(Message), [])

    async def test_link_must_be_site_relative(self):
        db = self._seed(1)
        for bad in ("javascript:alert(1)", "https://evil.example.com", "//evil.example.com"):
            with self.assertRaises(AppException, msg=f"应拒绝 {bad}"):
                await admin_message_service.send_system_message(
                    db, admin_id=99, title="t", content="c", link=bad
                )
        self.assertEqual(db.all_rows(Message), [])

        ok = await admin_message_service.send_system_message(
            db, admin_id=99, title="t", content="c", link="/tutorials/12"
        )
        self.assertEqual(ok["sent_count"], 1)
        self.assertEqual(db.all_rows(Message)[0]["link"], "/tutorials/12")

    async def test_invalid_target_rejected(self):
        db = self._seed(1)
        with self.assertRaises(AppException):
            await admin_message_service.send_system_message(
                db, admin_id=99, title="t", content="c", target="nobody"
            )

    async def test_no_receivers_rejected(self):
        """全站但库里没有任何活跃用户时，明确报错而不是「成功发送 0 人」。"""
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await admin_message_service.send_system_message(
                db, admin_id=99, title="t", content="c", target="all"
            )
        self.assertEqual(ctx.exception.code, 400)


class MessageBatchListTests(unittest.IsolatedAsyncioTestCase):
    """批次列表的**结果映射**（SQL 聚合本身不在离线单测覆盖范围内，见类注释）。"""

    class _Row:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class _AggSession:
        """只回答 `list_message_batches` 的两次查询：先取批次号列表，再取聚合行。

        ⚠️ 为什么不直接跑真实 SQL：测试替身 `fake_db._do_select_multi` 精确支持
        「分组列 + 单个 func.count()」，而这里用了 `min/sum(case())` 多个聚合，
        替身会显式报错（这是刻意设计：返回错数据比报错危险）。
        因此本用例只覆盖「聚合行 → API 结构」的映射与 total 计算；
        SQL 本身由本地真实库验证过（全站 4 人 → 4 行、定向 1 人 → 1 行、已读数 1、批次 2）。
        """

        def __init__(self, batch_ids, rows):
            self._batch_ids = batch_ids
            self._rows = rows
            self.calls = 0

        async def execute(self, _stmt):
            self.calls += 1
            if self.calls == 1:
                batch_ids = self._batch_ids

                class _Scalars:
                    def all(self_inner):
                        return list(batch_ids)

                class _Result:
                    def scalars(self_inner):
                        return _Scalars()

                return _Result()
            rows = self._rows

            class _RowsResult:
                def all(self_inner):
                    return list(rows)

            return _RowsResult()

    async def test_batch_rows_are_mapped(self):
        import datetime as dt

        rows = [
            self._Row(
                batch_id="b2", title="定向通知", content="只发给 1", link=None,
                sent_count=1, read_count=0, created_at=dt.datetime(2026, 10, 28, 1, 0),
            ),
            self._Row(
                batch_id="b1", title="全站通知", content="发给所有人", link="/tutorials/1",
                sent_count=4, read_count=1, created_at=dt.datetime(2026, 10, 28, 0, 0),
            ),
        ]
        db = self._AggSession(["b2", "b1"], rows)
        result = await admin_message_service.list_message_batches(db, 1, 20)

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 20)
        first, second = result["items"]
        self.assertEqual(first["batch_id"], "b2")
        self.assertEqual(first["sent_count"], 1)
        self.assertEqual(first["link"], None)
        self.assertEqual(second["read_count"], 1)
        self.assertEqual(second["link"], "/tutorials/1")
        self.assertTrue(second["created_at"].startswith("2026-10-28T00:00"))
