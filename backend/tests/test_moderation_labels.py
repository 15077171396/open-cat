"""审核台的「展示名」映射与取值元组必须逐项对齐 —— 这条曾经真的漂了。

事故复盘：`CONTENT_TYPES` 在台账 P1-9 加了 `comment`（评论是公开 UGC，风险与昵称同级），
但管理端的 `CONTENT_TYPE_LABELS` 停在 8 个键、没跟上。于是审核台「内容类型」列
对每一条评论类留痕都直接显示成裸代码 `comment` —— 不报错、不白屏，只是难看且难懂，
一路没人发现，直到把映射搬回后端时才看见。

所以这里钉的是「**同一个词汇表不允许有两份手写副本**」：
- 四张 label 表 + 一张 reason 表的键集合必须与对应元组**完全相等**（多一个少一个都红）；
- label 不许直接把代码回填（那样守卫就失去意义）；
- 序列化输出里必须带 `*_label` 字段（前端只读它，不再自己查表）。

另外钉住两个筛选入口的校验：非法状态必须 400，而不是静默返回全部 ——
审核台「筛了待复核却看到已拦截」会让人误判队列已经清空。
"""

import unittest

from app.core.exceptions import AppException
from app.models.moderation import (
    CONTENT_TYPE_LABELS,
    CONTENT_TYPES,
    MODERATION_ACTION_LABELS,
    MODERATION_ACTIONS,
    MODERATION_STATUS_LABELS,
    MODERATION_STATUSES,
    REPORT_REASON_LABELS,
    REPORT_REASONS,
    REPORT_STATUS_LABELS,
    REPORT_STATUSES,
    ContentModerationLog,
    ContentReport,
)
from app.services.moderation_service import _log_to_dict, list_logs
from app.services.report_service import _report_to_dict, list_reports
from tests.fake_db import MemorySession


class LabelTablesAlignWithTuplesTests(unittest.TestCase):
    """五张表 ↔ 五个取值元组"""

    def _assert_aligned(self, labels: dict, values: tuple, name: str):
        self.assertEqual(set(labels), set(values), f"{name} 与取值元组漂移：{sorted(set(labels) ^ set(values))}")

    def test_content_type_labels(self):
        self._assert_aligned(CONTENT_TYPE_LABELS, CONTENT_TYPES, "CONTENT_TYPE_LABELS")

    def test_moderation_action_labels(self):
        self._assert_aligned(MODERATION_ACTION_LABELS, MODERATION_ACTIONS, "MODERATION_ACTION_LABELS")

    def test_moderation_status_labels(self):
        self._assert_aligned(MODERATION_STATUS_LABELS, MODERATION_STATUSES, "MODERATION_STATUS_LABELS")

    def test_report_status_labels(self):
        self._assert_aligned(REPORT_STATUS_LABELS, REPORT_STATUSES, "REPORT_STATUS_LABELS")

    def test_report_reason_labels(self):
        self._assert_aligned(REPORT_REASON_LABELS, REPORT_REASONS, "REPORT_REASON_LABELS")

    def test_labels_are_real_names_not_codes(self):
        """直接回填代码会让上面几条守卫变成自我欺骗"""
        for name, table in (
            ("CONTENT_TYPE_LABELS", CONTENT_TYPE_LABELS),
            ("MODERATION_ACTION_LABELS", MODERATION_ACTION_LABELS),
            ("MODERATION_STATUS_LABELS", MODERATION_STATUS_LABELS),
            ("REPORT_STATUS_LABELS", REPORT_STATUS_LABELS),
            ("REPORT_REASON_LABELS", REPORT_REASON_LABELS),
        ):
            for code, label in table.items():
                with self.subTest(table=name, code=code):
                    self.assertNotEqual(code, label)

    def test_comment_type_is_labeled(self):
        """回归本条：`comment` 曾是唯一没有中文名的类型"""
        self.assertEqual(CONTENT_TYPE_LABELS["comment"], "评论")


class SerializedItemsCarryLabelsTests(unittest.TestCase):
    """前端只读 *_label，不再自己查表"""

    def test_moderation_log_dict(self):
        item = _log_to_dict(
            ContentModerationLog(
                id=1, content_type="comment", target_id="42", user_id=7,
                excerpt="含敏感词的评论", hit_words="广告,加V", action="reject", status="rejected",
            )
        )
        self.assertEqual(item["content_type_label"], "评论")
        self.assertEqual(item["action_label"], "拒绝提交")
        self.assertEqual(item["status_label"], "已拦截")
        self.assertEqual(item["hit_words"], ["广告", "加V"], "命中词要拆成列表，前端直接渲染 Tag")

    def test_report_dict(self):
        item = _report_to_dict(
            ContentReport(
                id=2, reporter_id=8, content_type="comment", target_id="42",
                reason="spam", detail="一直发广告", status="pending",
            )
        )
        self.assertEqual(item["content_type_label"], "评论")
        self.assertEqual(item["status_label"], "待处理")
        self.assertEqual(item["reason_label"], "垃圾广告")

    def test_unknown_values_fall_back_to_the_code(self):
        """历史脏数据不该让接口 500，但也不该假装有中文名"""
        item = _log_to_dict(ContentModerationLog(id=3, content_type="legacy_type", action="reject", status="pending"))
        self.assertEqual(item["content_type_label"], "legacy_type")


def _seed_logs() -> MemorySession:
    db = MemorySession()
    db.seed(
        ContentModerationLog(id=1, content_type="comment", action="reject", status="rejected",
                             hit_words="广告", excerpt="评论一"),
        ContentModerationLog(id=2, content_type="nickname", action="mask", status="pending",
                             hit_words="加V", excerpt="昵称二"),
    )
    return db


class FilterValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_type_filter_narrows(self):
        db = _seed_logs()
        data = await list_logs(db, content_type="comment")
        self.assertEqual([i["id"] for i in data["items"]], [1])
        self.assertEqual(data["total"], 1)

    async def test_invalid_log_status_is_rejected(self):
        """静默回落成「不筛选」会让审核者以为队列已清空"""
        with self.assertRaises(AppException):
            await list_logs(_seed_logs(), status="done")

    async def test_invalid_report_status_is_rejected(self):
        with self.assertRaises(AppException):
            await list_reports(MemorySession(), status="closed")


if __name__ == "__main__":
    unittest.main()
