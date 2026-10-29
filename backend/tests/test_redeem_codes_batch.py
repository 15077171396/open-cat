"""兑换码**批量作废**：计数口径 / 跳过语义 / 审计。

兑换码是积分的**入金口** —— 一次能生成 100 个码，每个码对应
`points × max_uses` 的潜在积分债务。所以「作废」这件事有两个必须钉住的点：

1. **计数口径**：接口返回的 `disabled` 必须是「本次真正改动的条数」，
   而不是「请求里的条数」。混着几条已作废的码是常态（重试、重复勾选），
   把跳过的一起算进去，运营会以为撤销了 5 张其实只撤销了 3 张。
2. **审计必须落**：作废是不可逆的（没有「恢复」接口），事后只能靠审计回答
   「谁在什么时候作废了哪批码」。这条链路此前只覆盖单条作废。

另外钉住 rbac 路由登记 —— 管理端规则表是 **fail-closed**，
漏登记不会「权限放宽」，而是线上直接 403（见 tests/test_rbac.py 的覆盖守卫）。
"""

import unittest

from app.core import rbac as rbac_core
from app.core.exceptions import NotFoundError
from app.models.admin_audit_log import AdminAuditLog
from app.models.redemption_code import RedemptionCode
from app.services.redemption_service import disable_codes_batch
from tests.conftest import make_user
from tests.fake_db import MemorySession

ADMIN = make_user(id=4, account="admin@aicat.com", role="admin")


def _seed(*statuses: str) -> MemorySession:
    db = MemorySession()
    for index, status in enumerate(statuses, start=1):
        db.seed(
            RedemptionCode(
                id=index, code=f"AAAA-BBBB-CCCC-{index:04d}", points=100,
                max_uses=1, used_count=0, status=status, note=None, created_by=4,
            )
        )
    return db


class DisableCodesBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_counts_rows_actually_changed(self):
        """disabled = 真正改动的条数，不含已作废的 —— 否则运营会多算撤销量"""
        db = _seed("active", "disabled", "active")
        result = await disable_codes_batch(db, [1, 2, 3], ADMIN)
        self.assertEqual(result, {"disabled": 2, "skipped": 1})

    async def test_all_rows_end_up_disabled(self):
        db = _seed("active", "disabled", "active")
        await disable_codes_batch(db, [1, 2, 3], ADMIN)
        statuses = {row["id"]: row["status"] for row in db.all_rows(RedemptionCode)}
        self.assertEqual(statuses, {1: "disabled", 2: "disabled", 3: "disabled"})

    async def test_untouched_codes_keep_their_status(self):
        """只动选中的 —— 误伤没选的码等于把还在发放的活动码全废了"""
        db = _seed("active", "active", "active")
        await disable_codes_batch(db, [2], ADMIN)
        statuses = {row["id"]: row["status"] for row in db.all_rows(RedemptionCode)}
        self.assertEqual(statuses, {1: "active", 2: "disabled", 3: "active"})

    async def test_reports_skipped_when_everything_already_disabled(self):
        db = _seed("disabled", "disabled")
        result = await disable_codes_batch(db, [1, 2], ADMIN)
        self.assertEqual(result, {"disabled": 0, "skipped": 2})

    async def test_unknown_ids_raise_not_found(self):
        db = _seed("active")
        with self.assertRaises(NotFoundError):
            await disable_codes_batch(db, [999], ADMIN)

    async def test_writes_exactly_one_audit_row(self):
        """一批只写一行（明细在 detail）—— 逐条写会把审计表撑起来"""
        db = _seed("active", "active", "disabled")
        await disable_codes_batch(db, [1, 2, 3], ADMIN)
        logs = db.all_rows(AdminAuditLog)
        self.assertEqual(len(logs), 1)
        log = logs[0]
        self.assertEqual(log["action"], "redeem_code_disable")
        self.assertEqual(log["target_type"], "redeem_code")
        self.assertEqual(log["admin_id"], 4)
        self.assertEqual(log["admin_account"], "admin@aicat.com")
        self.assertEqual(log["detail"]["disabled"], 2)
        self.assertEqual(log["detail"]["skipped"], 1)
        self.assertEqual(log["detail"]["requested"], 3)

    async def test_audit_truncates_a_very_long_id_list(self):
        """>50 个 id 时末尾用 -1 占位（detail 只用于定位，不需要完整名单）"""
        db = _seed(*(["active"] * 60))
        await disable_codes_batch(db, list(range(1, 61)), ADMIN)
        log = db.all_rows(AdminAuditLog)[0]
        self.assertEqual(len(log["detail"]["ids"]), 51)
        self.assertEqual(log["detail"]["ids"][-1], -1)
        self.assertEqual(log["detail"]["disabled"], 60)


class BatchDisableRoutePermissionTests(unittest.TestCase):
    """新路由必须登记权限规则：fail-closed 下漏登记 = 线上 403（不是放宽）"""

    def test_route_requires_disable_permission(self):
        self.assertEqual(
            rbac_core.match_route_permission(
                "POST", "/api/v1/admin/redemption-codes/batch-disable"
            ),
            ["redeem_code:disable"],
        )

    def test_batch_route_does_not_shadow_or_get_shadowed(self):
        """字面量路由与参数路由不得互相抢匹配（规则表是「先匹配者胜出」）"""
        self.assertEqual(
            rbac_core.match_route_permission("POST", "/api/v1/admin/redemption-codes"),
            ["redeem_code:create"],
        )
        self.assertEqual(
            rbac_core.match_route_permission(
                "PATCH", "/api/v1/admin/redemption-codes/123/disable"
            ),
            ["redeem_code:disable"],
        )


if __name__ == "__main__":
    unittest.main()
