"""评论回复通知：回复他人评论时，被回复者应在站内消息里收到一条 `type=comment`。

三个容易漏掉的判断，都在这里钉住：
1. **自己回自己不通知** —— 那是「补充说明」，不是「别人回应了你」；
2. **回复「某条回复」时，通知的是那条回复的作者**，不是顶层评论作者
   （`parent_id` 会被提升到顶层，通知对象必须在提升前取）；
3. **收件人已注销不通知** —— 给软删用户写消息既无意义，还会污染未读统计。
"""

import unittest

from app.models.message import Message
from app.services import comment_service
from app.services.storage import storage
from tests.fake_db import MemorySession
from tests.test_comments import make_tutorial, make_user


class CommentReplyNotifyTests(unittest.IsolatedAsyncioTestCase):
    async def _seed(self, db: MemorySession):
        author = make_user(1)
        replier = make_user(2)
        tutorial = make_tutorial(100)
        db.seed(author, replier, tutorial)
        return author, replier, tutorial

    async def test_reply_creates_message_for_parent_author(self):
        db = MemorySession()
        author, replier, tutorial = await self._seed(db)
        parent = await comment_service.create_comment(
            db, author, "tutorial", tutorial.id, "这是顶层评论"
        )
        await comment_service.create_comment(
            db, replier, "tutorial", tutorial.id, "这是回复内容", parent_id=parent["id"]
        )

        rows = db.all_rows(Message)
        self.assertEqual(len(rows), 1, "回复应产生且只产生一条通知")
        msg = rows[0]
        self.assertEqual(msg["user_id"], author.id, "通知应发给被回复的评论作者")
        self.assertEqual(msg["sender_id"], replier.id)
        self.assertEqual(msg["type"], "comment")
        self.assertIn("回复了你的评论", msg["title"])
        self.assertEqual(msg["content"], "这是回复内容")
        self.assertEqual(msg["link"], f"/tutorials/{tutorial.id}", "通知应能点回原内容")
        self.assertFalse(msg["is_read"])

    async def test_reply_to_reply_notifies_direct_target(self):
        """回复「某条回复」时，通知对象是那条回复的作者，而不是顶层评论作者。"""
        db = MemorySession()
        author, replier, tutorial = await self._seed(db)
        third = make_user(3)
        db.seed(third)

        top = await comment_service.create_comment(
            db, author, "tutorial", tutorial.id, "顶层评论"
        )
        first_reply = await comment_service.create_comment(
            db, replier, "tutorial", tutorial.id, "第一层回复", parent_id=top["id"]
        )
        # 顶层作者再回复「第一层回复」
        await comment_service.create_comment(
            db, author, "tutorial", tutorial.id, "回复那条回复", parent_id=first_reply["id"]
        )

        rows = db.all_rows(Message)
        self.assertEqual(len(rows), 2, "两次回复各产生一条通知")
        # 第二条通知应发给 replier（被直接回复的人），而不是 author 自己
        self.assertEqual(rows[-1]["user_id"], replier.id)
        self.assertEqual(rows[-1]["content"], "回复那条回复")

    async def test_self_reply_does_not_notify(self):
        db = MemorySession()
        author, _, tutorial = await self._seed(db)
        top = await comment_service.create_comment(
            db, author, "tutorial", tutorial.id, "顶层评论"
        )
        await comment_service.create_comment(
            db, author, "tutorial", tutorial.id, "我自己补充一句", parent_id=top["id"]
        )
        self.assertEqual(db.all_rows(Message), [], "自己回自己不该产生通知")

    async def test_image_only_reply_notifies_with_placeholder(self):
        db = MemorySession()
        author, replier, tutorial = await self._seed(db)
        parent = await comment_service.create_comment(
            db, author, "tutorial", tutorial.id, "顶层评论"
        )
        # 只发图不写字是合法评论（见 comment_service docstring）
        

        prefix = storage.public_url_prefixes()[0]
        await comment_service.create_comment(
            db,
            replier,
            "tutorial",
            tutorial.id,
            "",
            parent_id=parent["id"],
            images=[f"{prefix}comments/x.webp"],
        )
        rows = db.all_rows(Message)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "（图片）", "纯图回复的通知正文应有占位文案")

    async def test_deleted_receiver_is_skipped(self):
        """被回复者已注销时不写通知（否则未读统计里会长期挂着一个不存在的人）。

        直接测通知函数而不是走 create_comment：测试替身 `MemorySession` 存的是列值快照，
        把 ORM 对象的 `deleted_at` 改成非空后，查询仍看不到 —— 那样测出来的是替身的局限，
        不是被测逻辑。这里用「查不到用户」的替身精确表达前提。
        """

        class _NoUserSession:
            def __init__(self):
                self.added = []

            async def execute(self, *_args, **_kwargs):
                class _Result:
                    def first(self_inner):
                        return None

                return _Result()

            def add(self, obj):
                self.added.append(obj)

        db = _NoUserSession()
        replier = make_user(2)
        await comment_service._notify_comment_reply(  # noqa: SLF001 测的就是这个函数
            db,
            receiver_id=1,
            sender=replier,
            target_type="tutorial",
            target_id=100,
            content="回复一个已注销用户",
            images=[],
        )
        self.assertEqual(db.added, [], "已注销用户不该收到站内消息")

    async def test_top_level_comment_does_not_notify(self):
        db = MemorySession()
        author, _, tutorial = await self._seed(db)
        await comment_service.create_comment(db, author, "tutorial", tutorial.id, "普通评论")
        self.assertEqual(db.all_rows(Message), [], "顶层评论没有回复对象，不该发通知")
