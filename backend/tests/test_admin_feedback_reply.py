"""反馈工单化（回复闭环）的服务层测试。

钉四条不变量：

1. **管理员回复 = 用户可见 + 通知 + 审计，且三者同事务**。回复是面向用户的
   触达动作（与 admin_note 的内部备注相对），漏通知或漏审计都是事故；
2. **状态自动流转方向**：管理员回复把待处理拉起为处理中（回复即跟进）；
   用户回复把已解决重新拉起（用户说「还是不行」不该逼他重开新单）；
3. **已关闭是终态**：任何一方的回复都被拒收 —— 否则「关闭」失去含义；
4. **回复数一次聚合**：列表页的 reply_count 不做逐行 N+1。
"""

import datetime as dt
import unittest

from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.feedback import Feedback
from app.models.feedback_reply import FeedbackReply
from app.models.message import Message
from app.models.user import User
from app.services import feedback_service as fs
from app.services import admin_feedback_service as afs
from tests.fake_db import MemorySession

NOW = dt.datetime(2026, 11, 3, 12, 0, 0)


def _db_with_feedback(status: str = "pending", *, feedback_id: int = 501, user_id: int = 7) -> MemorySession:
    db = MemorySession()
    db.seed(
        User(id=1, account="admin@example.com", password_hash="x", role="admin"),
        User(id=user_id, account="user@example.com", password_hash="x", role="user"),
        Feedback(
            id=feedback_id, user_id=user_id, content="页面打不开",
            status=status, created_at=NOW, updated_at=NOW,
        ),
    )
    return db


class AdminReplyTests(unittest.IsolatedAsyncioTestCase):
    """管理员回复：可见线程 + 通知 + 审计 + 状态流转"""

    async def test_reply_creates_thread_notification_and_audit(self):
        db = _db_with_feedback("pending")
        admin = User(id=1, account="admin@example.com", password_hash="x", role="admin")

        reply = await afs.admin_add_reply(db, admin, 501, "已收到，正在排查")

        self.assertEqual(reply["sender"], "admin")
        self.assertEqual(reply["content"], "已收到，正在排查")
        # 回复入库
        replies = db.all_rows(FeedbackReply)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["feedback_id"], 501)
        # 用户收到站内通知（type 复用 system 合法值）
        msgs = db.all_rows(Message)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["user_id"], 7)
        self.assertEqual(msgs[0]["type"], "system")
        self.assertIn("回复", msgs[0]["title"])
        # 待处理 → 处理中（回复即跟进）
        feedback_row = next(r for r in db.all_rows(Feedback) if r["id"] == 501)
        self.assertEqual(feedback_row["status"], "processing")
        # 审计留痕
        audits = [r for r in db.all_rows(AdminAuditLog) if r["action"] == "feedback_reply"]
        self.assertEqual(len(audits), 1, "回复必须与回复本身同事务写入审计")
        self.assertEqual(audits[0]["target_type"], "feedback")

    async def test_reply_on_resolved_keeps_status(self):
        """管理员在已解决单上补回复：状态不倒退（resolved 是管理侧的结论）"""
        db = _db_with_feedback("resolved")
        admin = User(id=1, account="admin@example.com", password_hash="x", role="admin")
        await afs.admin_add_reply(db, admin, 501, "补充说明一下")
        feedback_row = next(r for r in db.all_rows(Feedback) if r["id"] == 501)
        self.assertEqual(feedback_row["status"], "resolved")

    async def test_reply_404_on_missing_feedback(self):
        db = _db_with_feedback()
        admin = User(id=1, account="admin@example.com", password_hash="x", role="admin")
        with self.assertRaises(AppException) as ctx:
            await afs.admin_add_reply(db, admin, 99999, "hello")
        self.assertEqual(ctx.exception.code, 404)

    async def test_reply_rejects_blank_and_overlength(self):
        db = _db_with_feedback()
        admin = User(id=1, account="admin@example.com", password_hash="x", role="admin")
        with self.assertRaises(AppException) as ctx:
            await afs.admin_add_reply(db, admin, 501, "   ")
        self.assertEqual(ctx.exception.code, 400)
        with self.assertRaises(AppException) as ctx:
            await afs.admin_add_reply(db, admin, 501, "超" * (fs.REPLY_MAX_LENGTH + 1))
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.all_rows(FeedbackReply), [], "校验失败不得产生任何写入")


class UserReplyTests(unittest.IsolatedAsyncioTestCase):
    """用户回复：重开已解决单；已关闭拒收；只能回复自己的工单"""

    async def test_user_reply_reopens_resolved(self):
        db = _db_with_feedback("resolved")
        reply = await fs.add_reply(
            db, feedback_id=501, sender="user", sender_id=7,
            content="还是不行", notify_user=False,
        )
        self.assertEqual(reply["sender"], "user")
        feedback_row = next(r for r in db.all_rows(Feedback) if r["id"] == 501)
        self.assertEqual(feedback_row["status"], "processing", "用户回复把已解决单重新拉起")
        # 用户侧回复不发通知（通知只有管理员回复才有意义）
        self.assertEqual(db.all_rows(Message), [])

    async def test_user_reply_on_closed_refused(self):
        db = _db_with_feedback("closed")
        with self.assertRaises(AppException) as ctx:
            await fs.add_reply(
                db, feedback_id=501, sender="user", sender_id=7,
                content="再看看", notify_user=False,
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.all_rows(FeedbackReply), [])

    async def test_user_reply_on_missing_feedback_refused(self):
        db = _db_with_feedback()
        with self.assertRaises(AppException) as ctx:
            await fs.add_reply(
                db, feedback_id=404, sender="user", sender_id=7,
                content="hi", notify_user=False,
            )
        self.assertEqual(ctx.exception.code, 404)

    async def test_user_reply_moderation_reject_keeps_content_out(self):
        """命中敏感词且处置为拒绝：回复不入库，留痕单独提交"""
        from app.services import moderation_service as ms
        from unittest import mock

        db = _db_with_feedback("processing")
        with mock.patch.object(ms, "screen_text") as screen:
            screen.return_value = mock.Mock(hits=["bad"], action="reject", passed=False, text="")
            with self.assertRaises(AppException) as ctx:
                await fs.add_reply(
                    db, feedback_id=501, sender="user", sender_id=7,
                    content="含违禁词", notify_user=False,
                )
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.all_rows(FeedbackReply), [], "被拒内容不得入库")
        self.assertEqual(db.all_rows(Message), [])


class ReplyAggregationTests(unittest.IsolatedAsyncioTestCase):
    """线程读取与列表聚合"""

    async def test_thread_and_counts(self):
        db = _db_with_feedback("processing")
        db.seed(
            FeedbackReply(id=1, feedback_id=501, sender="user", content="打不开",
                          created_at=NOW),
            FeedbackReply(id=2, feedback_id=501, sender="admin", content="在看",
                          created_at=NOW + dt.timedelta(minutes=1)),
            FeedbackReply(id=3, feedback_id=999, sender="user", content="别处",
                          created_at=NOW),
        )
        thread = await fs.list_replies(db, 501)
        self.assertEqual([r["sender"] for r in thread], ["user", "admin"], "线程按时间升序")

        counts = await fs.reply_counts_for(db, [501, 999, 12345])
        self.assertEqual(counts, {501: 2, 999: 1}, "缺失反馈计数为 0（不进字典）")

    async def test_my_feedbacks_carries_replies(self):
        db = _db_with_feedback("processing")
        db.seed(FeedbackReply(id=1, feedback_id=501, sender="admin", content="在看",
                              created_at=NOW))
        # 别人的反馈不该出现在我的列表里
        db.seed(Feedback(id=502, user_id=8, content="别人的", status="pending",
                         created_at=NOW, updated_at=NOW))
        result = await fs.list_my_feedbacks(db, 7)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["replies"][0]["content"], "在看")

    async def test_admin_detail_includes_thread(self):
        db = _db_with_feedback("processing")
        db.seed(FeedbackReply(id=1, feedback_id=501, sender="user", content="打不开",
                              created_at=NOW))
        detail = await afs.admin_get_feedback(db, 501)
        self.assertEqual(detail["status_label"], "处理中")
        self.assertEqual(detail["replies"][0]["sender"], "user")


if __name__ == "__main__":
    unittest.main()
