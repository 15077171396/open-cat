"""内容审核链路测试（缺口台账 P0-5）。

ICP 备案站点提供 UGC，审核是合规底线而不是可选项。这里守三条底线：

1. **绕过无效**：全角/大小写/空格与标点干扰必须归一化后再匹配。只做字符串
   包含的话，「加 微 信」这类拆分写法会直接穿过去——那等于没有过滤。
2. **处置动作语义正确**：reject 必须拦下内容；mask 打码后放行；review 原样
   放行但留痕待复核；总开关关闭时整条链路跳过。
3. **留痕不放大风险**：只存摘要不存全文，且未命中不写日志（否则表随 UGC
   量线性膨胀，反而淹没有效信号）。

举报侧守两条：**同一内容不能重复举报**（防刷）、**单用户每日上限**（防批量
恶意举报灌满审核队列）。
"""

import datetime as dt
import unittest

from app.config import settings
from app.core.exceptions import AppException
from app.models.moderation import (
    ContentModerationLog,
    ContentReport,
    EXCERPT_MAX_LENGTH,
    MAX_REPORTS_PER_USER_PER_DAY,
)
from app.services import moderation_service as ms
from app.services import report_service as rs
from tests.fake_db import MemorySession


def make_user(id: int = 1) -> object:
    from app.models.user import User

    return User(id=id, account=f"u{id}@example.com", password_hash="x", role="user")


class NormalizeTests(unittest.TestCase):
    """归一化：绕过手法必须失效"""

    def test_fullwidth_and_case_normalized(self):
        self.assertEqual(ms.normalize("ＡBｃ"), "abc")

    def test_separators_stripped(self):
        for variant in ("加微信", "加 微 信", "加-微-信", "加*微*信", "加　微　信"):
            self.assertEqual(ms.normalize(variant), "加微信")

    def test_empty_safe(self):
        self.assertEqual(ms.normalize(""), "")
        self.assertEqual(ms.normalize(None), "")


class ScanTests(unittest.TestCase):
    """命中判定与返回原始词"""

    def test_scan_returns_original_word(self):
        hits = ms.scan("需要的话加微信详聊")
        self.assertIn("加微信", hits)

    def test_bypass_variants_all_hit(self):
        for variant in ("加 微 信", "加-微-信", "加微　信"):
            self.assertTrue(ms.scan(f"联系我{variant}"), f"绕过写法未命中: {variant}")

    def test_clean_text_no_hit(self):
        self.assertEqual(ms.scan("这篇教程讲得很清楚，谢谢分享"), [])

    def test_empty_word_never_matches_everything(self):
        """词库为空时正则不得退化为匹配空串（否则全站内容都会被拦下）"""
        orig_words, orig_extra = ms.DEFAULT_WORDS, settings.MODERATION_EXTRA_WORDS
        try:
            ms.DEFAULT_WORDS = ()
            settings.MODERATION_EXTRA_WORDS = ""
            ms.reload_words()
            self.assertEqual(ms.scan("一篇完全正常的教程"), [])
            self.assertTrue(ms.screen_text("一篇完全正常的教程").passed)
        finally:
            ms.DEFAULT_WORDS = orig_words
            settings.MODERATION_EXTRA_WORDS = orig_extra
            ms.reload_words()

    def test_extra_words_from_settings(self):
        """运营追加的词经环境变量生效，无需发版"""
        orig = settings.MODERATION_EXTRA_WORDS
        try:
            settings.MODERATION_EXTRA_WORDS = "内部渠道,特殊优惠"
            ms.reload_words()
            self.assertIn("内部渠道", ms.scan("走内部渠道拿货"))
        finally:
            settings.MODERATION_EXTRA_WORDS = orig
            ms.reload_words()


class ScreenActionTests(unittest.IsolatedAsyncioTestCase):
    """四种处置动作的语义"""

    def tearDown(self):
        ms.reload_words()

    async def test_reject_blocks_content(self):
        result = ms.screen_text("加微信详聊", action="reject")
        self.assertFalse(result.passed)
        self.assertTrue(result.hits)
        # reject 下文本原样返回（调用方应拒绝提交，无需打码）
        self.assertEqual(result.text, "加微信详聊")

    async def test_mask_returns_masked_text_and_passes(self):
        result = ms.screen_text("加微信详聊", action="mask")
        self.assertTrue(result.passed)
        self.assertNotIn("加微信", result.text)
        self.assertIn("***", result.text)

    async def test_review_passes_through(self):
        result = ms.screen_text("加微信详聊", action="review")
        self.assertTrue(result.passed)
        self.assertEqual(result.text, "加微信详聊")

    async def test_disabled_switch_skips_all(self):
        orig = settings.MODERATION_ENABLED
        try:
            settings.MODERATION_ENABLED = False
            result = ms.screen_text("加微信详聊", action="reject")
            self.assertTrue(result.passed)
            self.assertEqual(result.hits, [])
            self.assertEqual(result.action, "disabled")
        finally:
            settings.MODERATION_ENABLED = orig

    async def test_clean_text_action_pass(self):
        result = ms.screen_text("正常的技术讨论")
        self.assertTrue(result.passed)
        self.assertEqual(result.action, "pass")

    async def test_invalid_action_falls_back_to_reject(self):
        """配置写错时按最严处置，而不是放行"""
        result = ms.screen_text("加微信", action="not-a-real-action")
        self.assertFalse(result.passed)


class LogHitTests(unittest.IsolatedAsyncioTestCase):
    """命中留痕：只存摘要、状态与动作挂钩"""

    async def test_log_stores_excerpt_not_full_text(self):
        db = MemorySession()
        long_text = "前面都是废话" * 100 + "加微信"
        await ms.log_hit(
            db, content_type="feedback", user_id=7, text=long_text,
            hits=["加微信"], action="reject",
        )
        await db.flush()
        rows = db.all_rows(ContentModerationLog)
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows[0]["excerpt"]), EXCERPT_MAX_LENGTH + 1)
        self.assertNotEqual(rows[0]["excerpt"], long_text)

    async def test_reject_is_terminal_status(self):
        db = MemorySession()
        await ms.log_hit(
            db, content_type="nickname", user_id=7, text="加微信",
            hits=["加微信"], action="reject",
        )
        await db.flush()
        self.assertEqual(db.all_rows(ContentModerationLog)[0]["status"], "rejected")

    async def test_mask_and_review_need_human_eyes(self):
        for action in ("mask", "review"):
            db = MemorySession()
            await ms.log_hit(
                db, content_type="bio", user_id=7, text="加微信",
                hits=["加微信"], action=action,
            )
            await db.flush()
            self.assertEqual(
                db.all_rows(ContentModerationLog)[0]["status"], "pending",
                f"{action} 处置后应待人工复核",
            )

    async def test_no_hit_means_no_log(self):
        """未命中不落库：全量留痕会让表随 UGC 量膨胀，淹没有效信号"""
        db = MemorySession()
        result = ms.screen_text("完全正常的内容")
        self.assertEqual(result.hits, [])
        await db.flush()
        self.assertEqual(db.all_rows(ContentModerationLog), [])

    async def test_review_log_accepts_and_records_reviewer(self):
        db = MemorySession()
        await ms.log_hit(
            db, content_type="bio", user_id=7, text="加微信",
            hits=["加微信"], action="review", target_id="7",
        )
        await db.flush()
        log_id = db.all_rows(ContentModerationLog)[0]["id"]
        updated = await ms.review_log(db, log_id, reviewer_id=99, status="approved", note="误报")
        self.assertEqual(updated["status"], "approved")
        self.assertEqual(updated["reviewer_id"], 99)
        self.assertEqual(updated["review_note"], "误报")

    async def test_review_rejects_invalid_status(self):
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await ms.review_log(db, 1, 99, "whatever")
        self.assertEqual(ctx.exception.code, 400)


class FeedbackIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """反馈提交链路：这是最高频的 UGC 入口，审核必须真的生效"""

    def setUp(self):
        self._orig = settings.MODERATION_ACTION
        settings.MODERATION_ACTION = "reject"

    def tearDown(self):
        settings.MODERATION_ACTION = self._orig
        ms.reload_words()

    async def test_blocked_content_not_stored_but_logged(self):
        """违规反馈本体不入库，但留痕必须留下——否则管理员看不到有人在发"""
        from app.models.feedback import Feedback
        from app.services import feedback_service

        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await feedback_service.submit_feedback(db, 7, "教程不错，加微信细聊", None)
        self.assertEqual(ctx.exception.code, 400)
        await db.flush()
        self.assertEqual(db.all_rows(Feedback), [], "违规反馈不应入库")
        self.assertEqual(len(db.all_rows(ContentModerationLog)), 1)

    async def test_contact_field_is_also_screened(self):
        """联系方式是引流高发位，只查正文等于漏掉一半"""
        from app.services import feedback_service

        db = MemorySession()
        with self.assertRaises(AppException):
            await feedback_service.submit_feedback(db, 7, "讲得很好，谢谢", "加微信")
        await db.flush()
        self.assertEqual(len(db.all_rows(ContentModerationLog)), 1)

    async def test_clean_feedback_passes(self):
        from app.models.feedback import Feedback
        from app.services import feedback_service

        db = MemorySession()
        await feedback_service.submit_feedback(db, 7, "希望出一期关于 RAG 的教程", "mail@example.com")
        await db.flush()
        self.assertEqual(len(db.all_rows(Feedback)), 1)
        # 未命中不留痕：否则表随 UGC 量线性膨胀
        self.assertEqual(db.all_rows(ContentModerationLog), [])

    async def test_mask_action_stores_masked_content(self):
        from app.models.feedback import Feedback
        from app.services import feedback_service

        settings.MODERATION_ACTION = "mask"
        ms.reload_words()
        db = MemorySession()
        await feedback_service.submit_feedback(db, 7, "加微信细聊", None)
        await db.flush()
        rows = db.all_rows(Feedback)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("加微信", rows[0]["content"])
        # 打码放行的内容仍需人工看一眼
        self.assertEqual(db.all_rows(ContentModerationLog)[0]["status"], "pending")


    async def test_daily_quota_blocks_the_eleventh_feedback(self):
        """反馈曾是全站唯一没有配额的 UGC 入口（第四轮审查 P2-48）。

        单账号靠全局 300 req/min 认证限流就能日灌数十万行，每行 ≤2000 字符，
        命中敏感词时还各带一条留痕 —— 既埋掉真实举报，又在 2G 主机上无界涨表。
        """
        from datetime import datetime, timezone

        from app.models.feedback import Feedback
        from app.services import feedback_service as fs

        db = MemorySession()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        for i in range(fs.MAX_FEEDBACKS_PER_USER_PER_DAY):
            db.seed(Feedback(id=900_000 + i, user_id=7, content="正常反馈",
                             status="pending", created_at=now_utc))

        with self.assertRaises(AppException) as ctx:
            await fs.submit_feedback(db, 7, "超额度再提一条", None)
        self.assertEqual(ctx.exception.code, 429)
        self.assertEqual(
            len([r for r in db.all_rows(Feedback) if r["user_id"] == 7]),
            fs.MAX_FEEDBACKS_PER_USER_PER_DAY,
            "超限请求不该产生任何写入（含审核留痕之外的本体行）",
        )

    async def test_quota_counts_only_this_user_and_this_utc_day(self):
        """额度按「本人 + 当前 UTC 自然日」计：别人的、昨天的都不占额度。"""
        from datetime import datetime, timedelta, timezone

        from app.models.feedback import Feedback
        from app.services import feedback_service as fs

        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        db = MemorySession()
        n = fs.MAX_FEEDBACKS_PER_USER_PER_DAY
        for i in range(n):
            # 别人的 n 条
            db.seed(Feedback(id=910_000 + i, user_id=8, content="他人反馈",
                             status="pending", created_at=today_start))
            # 本人昨天 23:59 的 n 条（跨过 UTC 零点即重置）
            db.seed(Feedback(id=920_000 + i, user_id=7, content="昨日反馈",
                             status="pending", created_at=today_start - timedelta(minutes=1)))

        await fs.submit_feedback(db, 7, "新的一天的第一条", None)
        await db.flush()
        mine_today = [
            r for r in db.all_rows(Feedback)
            if r["user_id"] == 7 and r["created_at"] >= today_start
        ]
        self.assertEqual(len(mine_today), 1)


class ReportTests(unittest.IsolatedAsyncioTestCase):
    """举报：防刷 + 处理流转"""

    def _submit(self, db, reporter_id=1, target_id="42", reason="spam"):
        return rs.submit_report(
            db, reporter_id, content_type="tutorial",
            target_id=target_id, reason=reason, detail="内容有问题",
        )

    async def test_submit_ok(self):
        db = MemorySession()
        row = await self._submit(db)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["reason_label"], "垃圾广告")

    async def test_duplicate_report_rejected(self):
        db = MemorySession()
        await self._submit(db)
        with self.assertRaises(AppException) as ctx:
            await self._submit(db)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(len(db.all_rows(ContentReport)), 1)

    async def test_daily_quota_enforced(self):
        db = MemorySession()
        for i in range(MAX_REPORTS_PER_USER_PER_DAY):
            await self._submit(db, target_id=str(i))
        with self.assertRaises(AppException) as ctx:
            await self._submit(db, target_id="overflow")
        self.assertEqual(ctx.exception.code, 429)

    async def test_invalid_type_and_reason_rejected(self):
        db = MemorySession()
        with self.assertRaises(AppException):
            await rs.submit_report(
                db, 1, content_type="unknown", target_id="1", reason="spam"
            )
        with self.assertRaises(AppException):
            await rs.submit_report(
                db, 1, content_type="tutorial", target_id="1", reason="because"
            )

    async def test_node_content_is_reportable(self):
        """百科节点前台可评论却不可举报（CONTENT_TYPES 曾缺 node）：
        人工兜底通道不该只覆盖教程与资源两类内容。"""
        db = MemorySession()
        row = await rs.submit_report(
            db, 1, content_type="node", target_id="7882461520", reason="spam"
        )
        self.assertEqual(row["content_type"], "node")
        self.assertEqual(row["status"], "pending")

    async def test_same_target_id_under_different_types_does_not_collide(self):
        """唯一约束是 (举报人, 类型, 对象)：教程 42 与节点 42 必须各自可举报一次。"""
        db = MemorySession()
        await self._submit(db, target_id="42")
        row = await rs.submit_report(
            db, 1, content_type="node", target_id="42", reason="abuse"
        )
        self.assertEqual(row["content_type"], "node")
        self.assertEqual(len(db.all_rows(ContentReport)), 2)

    async def test_handle_report_transitions_status(self):
        db = MemorySession()
        row = await self._submit(db)
        handled = await rs.handle_report(db, row["id"], handler_id=99, status="resolved", note="已删除")
        self.assertEqual(handled["status"], "resolved")
        self.assertEqual(handled["handler_id"], 99)
        # 二次处理必须拒绝，避免重复动作覆盖首次处理人
        with self.assertRaises(AppException) as ctx:
            await rs.handle_report(db, row["id"], handler_id=100, status="dismissed")
        self.assertEqual(ctx.exception.code, 400)

    async def test_pending_counts_for_badge(self):
        db = MemorySession()
        await self._submit(db, target_id="1")
        await self._submit(db, target_id="2")
        self.assertEqual(await rs.count_pending(db), 2)
        rows = await rs.list_reports(db)
        self.assertEqual(rows["total"], 2)

    async def test_detail_is_stripped_of_markup(self):
        db = MemorySession()
        row = await rs.submit_report(
            db, 1, content_type="tutorial", target_id="1",
            reason="other", detail="<script>alert(1)</script>他抄我的",
        )
        self.assertNotIn("<script>", row["detail"])


class ReportRouteMountedTest(unittest.TestCase):
    """举报路由必须真的挂上（曾出现「import 了但忘 include」的死代码）"""

    def test_report_routes_in_openapi(self):
        from app.main import app

        schema = app.openapi()
        paths = [p for p in schema["paths"] if p.startswith("/api/v1/reports")]
        self.assertTrue(paths, "举报路由未挂载：前台举报入口会 404")
        self.assertIn("/api/v1/reports/options", paths)


if __name__ == "__main__":
    unittest.main()
