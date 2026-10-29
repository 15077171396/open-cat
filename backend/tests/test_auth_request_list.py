"""认证申请列表：状态筛选、审核字段出口、展示名对齐。

改版前 `get_pending_auth_requests` **硬编码 `status == "pending"`**，于是审批过的记录
在后台彻底消失 —— `vip_duration` / `reviewed_by` / `reviewed_at` / `reject_reason`
这四列一直在库里，但没有任何出口。运营问「这个人谁批的、给了多久、为什么被驳回」
只能翻数据库。这里把「能按状态查 + 审核字段有出口」钉住。

另外钉两件事：
- **状态与 VIP 时长的中文名只有一份**（后端）。管理端另抄一份的话，加状态时不会报错，
  只会静默显示成裸代码（审核台的 `comment` 就这么错了一年）。
- **审核人用 outer join**：待审核时 `reviewed_by` 为 NULL，inner join 会把待审核记录
  整条吃掉 —— 那正是审核队列本身。
"""

import unittest

from app.core.exceptions import AppException
from app.models.auth_request import (
    AUTH_REQUEST_STATUSES,
    AUTH_REQUEST_STATUS_LABELS,
    VIP_DURATION_LABELS,
)
from app.services.audit_service import VIP_DURATIONS, list_auth_requests
from tests.test_points_ledger import _RecordingSession


class LabelAlignmentTests(unittest.TestCase):
    def test_status_labels_cover_every_status(self):
        self.assertEqual(set(AUTH_REQUEST_STATUS_LABELS), set(AUTH_REQUEST_STATUSES))

    def test_vip_duration_labels_cover_every_duration(self):
        """通过认证时可选的开通时长必须与 VIP_DURATIONS 的键一致，否则选了个后端不认的值"""
        self.assertEqual(set(VIP_DURATION_LABELS), set(VIP_DURATIONS))

    def test_labels_are_real_names_not_codes(self):
        for name, table in (
            ("AUTH_REQUEST_STATUS_LABELS", AUTH_REQUEST_STATUS_LABELS),
            ("VIP_DURATION_LABELS", VIP_DURATION_LABELS),
        ):
            for code, label in table.items():
                with self.subTest(table=name, code=code):
                    self.assertNotEqual(code, label)


class ListAuthRequestsTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_status_is_rejected_before_touching_db(self):
        db = _RecordingSession()
        with self.assertRaises(AppException):
            await list_auth_requests(db, status="done")
        self.assertEqual(db.calls, [], "校验必须在触库前完成")

    async def test_status_filter_reaches_both_queries(self):
        db = _RecordingSession()
        await list_auth_requests(db, status="approved")
        self.assertEqual(len(db.calls), 2)
        for sql, _ in db.calls:
            self.assertIn("auth_requests.status =", sql)

    async def test_all_means_no_status_condition(self):
        """`all` 与不传等价 —— 前端 Segmented 的「全部」传的是空串，这里挡一层"""
        db = _RecordingSession()
        await list_auth_requests(db, status="all")
        self.assertNotIn("auth_requests.status =", db.sql)

    async def test_reviewer_is_resolved_with_an_outer_join(self):
        """待审核时 reviewed_by 为 NULL —— inner join 会把审核队列本身吃光"""
        db = _RecordingSession()
        await list_auth_requests(db)
        list_sql = db.calls[1][0]
        self.assertIn("LEFT OUTER JOIN users", list_sql)
        self.assertIn("AS users_1", list_sql, "申请人/审核人是同一张表，必须有别名")
        # count 不带审核人 join（不影响行数）
        self.assertNotIn("LEFT OUTER JOIN", db.calls[0][0])

    async def test_no_filters_means_no_where_clause(self):
        db = _RecordingSession()
        await list_auth_requests(db)
        self.assertNotIn("WHERE", db.sql)

    async def test_ordering_is_fifo(self):
        """先到先审：按提交时间正序，再用 id 兜底保证同秒内翻页稳定"""
        db = _RecordingSession()
        await list_auth_requests(db)
        sql = db.calls[1][0]
        self.assertIn("ORDER BY auth_requests.created_at ASC, auth_requests.id ASC", sql)


if __name__ == "__main__":
    unittest.main()
