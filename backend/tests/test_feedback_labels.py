"""用户反馈：状态展示名与取值的对齐，以及后台列表的筛选下推。

两个要钉的点，都是「不报错但静默变糟」那一类：

1. **状态展示名只能有一份**。`FEEDBACK_STATUSES` 是写入端的事实来源，管理端若另抄一份
   「代码 → 中文名」的映射，新增状态时管理端不会报错，只会把裸代码（`processing`）直接
   显示在列表里 —— 审核台的 `comment` 就这么错了一整年。
2. **筛选必须真的进 SQL**。状态/时间区间若被静默忽略，运营会看到「筛了待处理却是全部」
   或「本周反馈为空」，据此判断队列已经清空。这里断言语句本身，而不是返回值
   （桩里的返回值是造的，看它等于自证）。
"""

import unittest
from datetime import datetime

from app.core.exceptions import AppException
from app.models.feedback import (
    FEEDBACK_STATUSES,
    FEEDBACK_STATUS_LABELS,
    Feedback,
)
from app.services.admin_feedback_service import admin_list_feedbacks
from app.services.feedback_service import to_dict
from tests.test_points_ledger import _RecordingSession


class StatusLabelTests(unittest.TestCase):
    def test_labels_cover_every_status(self):
        self.assertEqual(
            set(FEEDBACK_STATUS_LABELS),
            set(FEEDBACK_STATUSES),
            "状态展示名与 FEEDBACK_STATUSES 漂移：新增状态必须同时补中文名",
        )

    def test_labels_are_real_names_not_codes(self):
        """直接回填代码会让上一条守卫变成自我欺骗"""
        for code, label in FEEDBACK_STATUS_LABELS.items():
            with self.subTest(code=code):
                self.assertNotEqual(code, label)

    def test_to_dict_carries_the_label(self):
        """前端只读 status_label，不再自己查表"""
        item = to_dict(Feedback(id=1, user_id=7, content="页面打不开", status="processing"))
        self.assertEqual(item["status"], "processing")
        self.assertEqual(item["status_label"], "处理中")

    def test_unknown_status_falls_back_to_the_code(self):
        """历史脏数据不该让接口 500，但也不该假装有中文名"""
        item = to_dict(Feedback(id=2, user_id=7, content="x", status="legacy_status"))
        self.assertEqual(item["status_label"], "legacy_status")


class AdminListFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_status_is_rejected_before_touching_db(self):
        """静默回落成「不筛选」会让运营以为队列已清空"""
        db = _RecordingSession()
        with self.assertRaises(AppException):
            await admin_list_feedbacks(db, status="done")
        self.assertEqual(db.calls, [], "校验必须在触库前完成")

    async def test_status_filter_reaches_both_queries(self):
        db = _RecordingSession()
        await admin_list_feedbacks(db, status="pending")
        self.assertEqual(len(db.calls), 2)
        for sql, _ in db.calls:
            self.assertIn("feedbacks.status =", sql)

    async def test_time_range_is_inclusive_on_both_ends(self):
        start = datetime(2026, 10, 16, 16, 0, 0)
        end = datetime(2026, 10, 17, 15, 59, 59)
        db = _RecordingSession()
        await admin_list_feedbacks(db, start_at=start, end_at=end)
        self.assertIn("feedbacks.created_at >=", db.sql)
        self.assertIn("feedbacks.created_at <=", db.sql)
        # 绑定值必须是调用方给的 naive UTC（换算在路由层已完成，service 不得再动时区）
        self.assertIn(start, db.bound_values)
        self.assertIn(end, db.bound_values)

    async def test_keyword_escapes_like_wildcards(self):
        """不转义时搜 `%` 会匹配全部反馈（性能与语义双输）"""
        db = _RecordingSession()
        await admin_list_feedbacks(db, keyword="100%")
        self.assertIn("LIKE", db.sql)
        self.assertIn("ESCAPE", db.sql)

    async def test_no_filters_means_no_where_clause(self):
        db = _RecordingSession()
        await admin_list_feedbacks(db)
        for needle in ("feedbacks.status =", "feedbacks.created_at >=", "feedbacks.created_at <=", "WHERE"):
            self.assertNotIn(needle, db.sql)


if __name__ == "__main__":
    unittest.main()
