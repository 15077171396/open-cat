"""审计「动作码 / 目标类型 / 风险等级」三张表的一致性，以及风险筛选的下推。

为什么值得单独钉：这三张表都是**写入端与展示端的同一份事实来源**，任何一处漏项都不会报错：

- 新增动作码没同步 `AUDIT_ACTION_RISK` → 该动作在界面上显示成「常规」（蓝），
  **最该被看见的高危操作被静默降级**；
- 新增目标类型没同步 `AUDIT_TARGET_TYPE_LABELS` → 界面直接显示 `setting` 这类裸代码；
- 反过来，表里留着已删除的动作码 → 长期无人发现，等有人按它筛时已经没有数据。

风险筛选还有一层坑：等级**不是数据库列**，而是 `action → 等级` 的映射。
若取回来再用 Python 过滤，`total` 与分页就全错了 —— 这里断言它真的下推到了 SQL。
"""

import unittest
from datetime import datetime

from app.core.exceptions import AppException
from app.models.admin_audit_log import (
    AUDIT_ACTION_RISK,
    AUDIT_ACTIONS,
    AUDIT_RISKS,
    AUDIT_RISK_DANGER,
    AUDIT_RISK_MONEY,
    AUDIT_RISK_NORMAL,
    AUDIT_TARGET_TYPE_LABELS,
    AUDIT_TARGET_TYPES,
    AdminAuditLog,
)
from app.services.admin_audit_service import list_audit_logs
from tests.fake_db import MemorySession


class TaxonomyAlignmentTests(unittest.TestCase):
    """三张表必须与各自的主清单一一对应"""

    def test_every_action_has_a_risk_level(self):
        """漏归类 = 高危动作在界面上被显示成常规色，等于把最该看见的操作藏起来"""
        self.assertEqual(set(AUDIT_ACTION_RISK), set(AUDIT_ACTIONS))

    def test_risk_values_are_known_levels(self):
        for action, level in AUDIT_ACTION_RISK.items():
            with self.subTest(action=action):
                self.assertIn(level, AUDIT_RISKS)

    def test_every_level_is_reachable(self):
        """某个等级一条动作都没有 → 筛选它永远空，说明分级表被改坏了"""
        used = set(AUDIT_ACTION_RISK.values())
        for level in AUDIT_RISKS:
            with self.subTest(level=level):
                self.assertIn(level, used)

    def test_every_target_type_has_a_label(self):
        self.assertEqual(set(AUDIT_TARGET_TYPE_LABELS), set(AUDIT_TARGET_TYPES))

    def test_labels_are_not_just_the_code(self):
        """label 必须真的是中文名 —— 直接回填代码会让这个守卫失去意义"""
        for code, label in AUDIT_TARGET_TYPE_LABELS.items():
            with self.subTest(code=code):
                self.assertNotEqual(code, label)

    def test_high_risk_actions_are_classified_as_danger(self):
        """提权 / 不可逆 / 全站可见内容 —— 这几类是审计页最该被看见的"""
        for action in (
            "user_update_role", "user_delete", "user_batch_delete", "user_rbac_assign",
            "role_permission_update", "role_create", "role_delete",
            "content_delete", "announcement_delete", "settings_update",
        ):
            with self.subTest(action=action):
                self.assertEqual(AUDIT_ACTION_RISK[action], AUDIT_RISK_DANGER)

    def test_money_actions_are_classified_as_money(self):
        for action in (
            "user_adjust_points", "user_update_vip", "auth_approve", "auth_reject",
            "redeem_code_create", "redeem_code_disable",
        ):
            with self.subTest(action=action):
                self.assertEqual(AUDIT_ACTION_RISK[action], AUDIT_RISK_MONEY)

    def test_routine_edits_are_not_flagged(self):
        """反过来也要钉：常规编辑不该被标成高危，否则红色满屏就没有重点了"""
        for action in ("content_create", "content_update", "announcement_update", "user_create"):
            with self.subTest(action=action):
                self.assertEqual(AUDIT_ACTION_RISK[action], AUDIT_RISK_NORMAL)


def _seed() -> MemorySession:
    db = MemorySession()
    db.seed(
        AdminAuditLog(id=1, admin_id=4, admin_account="admin@aicat.com",
                      action="content_delete", target_type="tutorial", target_id=11,
                      summary="删除内容 A", created_at=datetime(2026, 10, 13, 6, 0, 0)),
        AdminAuditLog(id=2, admin_id=4, admin_account="admin@aicat.com",
                      action="content_update", target_type="node", target_id=12,
                      summary="编辑内容 B", created_at=datetime(2026, 10, 14, 6, 0, 0)),
        AdminAuditLog(id=3, admin_id=9, admin_account="ops@aicat.com",
                      action="user_adjust_points", target_type="user", target_id=5,
                      summary="调整用户积分", created_at=datetime(2026, 10, 15, 6, 0, 0)),
    )
    return db


class ListAuditLogsFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_risk_danger_only_returns_danger_actions(self):
        db = _seed()
        data = await list_audit_logs(db, risk=AUDIT_RISK_DANGER)
        self.assertEqual([i["action"] for i in data["items"]], ["content_delete"])
        self.assertEqual(data["total"], 1, "total 必须跟着风险筛选走")

    async def test_risk_money_and_normal(self):
        self.assertEqual((await list_audit_logs(_seed(), risk=AUDIT_RISK_MONEY))["total"], 1)
        self.assertEqual((await list_audit_logs(_seed(), risk=AUDIT_RISK_NORMAL))["total"], 1)

    async def test_all_risk_passes_through(self):
        self.assertEqual((await list_audit_logs(_seed(), risk="all"))["total"], 3)

    async def test_unknown_risk_is_rejected_before_touching_db(self):
        """拼错参数必须 400：静默回落成「不筛选」会让运营以为在看高危，其实看的是全部"""
        from tests.test_points_ledger import _RecordingSession

        db = _RecordingSession()
        with self.assertRaises(AppException):
            await list_audit_logs(db, risk="critical")
        self.assertEqual(db.calls, [])

    async def test_risk_is_pushed_down_to_sql_not_filtered_in_python(self):
        """等级不是列。若用 Python 过滤，分页与 total 会错 —— 断言语句里真有 IN 条件"""
        from tests.test_points_ledger import _RecordingSession

        db = _RecordingSession(total=1)
        await list_audit_logs(db, risk=AUDIT_RISK_DANGER)
        self.assertIn("admin_audit_logs.action IN", db.sql)
        self.assertEqual(len(db.calls), 2)
        for sql, _ in db.calls:
            self.assertIn("admin_audit_logs.action IN", sql, "count 与列表两条语句都要带上")

    async def test_time_range_is_inclusive_on_both_ends(self):
        start = datetime(2026, 10, 13, 0, 0, 0)
        end = datetime(2026, 10, 14, 23, 59, 59)
        data = await list_audit_logs(_seed(), start_at=start, end_at=end)
        self.assertEqual([i["id"] for i in data["items"]], [2, 1])

    async def test_items_carry_backend_owned_display_fields(self):
        """中文名与风险等级由后端给出，前端不再自己维护一份会漂移的映射"""
        items = (await list_audit_logs(_seed()))["items"]
        # 列表按时间倒序，别假定 items[0] 是哪一条 —— 按动作码定位
        item = next(i for i in items if i["action"] == "content_delete")
        self.assertEqual(item["target_type_label"], "教程")
        self.assertEqual(item["risk_level"], AUDIT_RISK_DANGER)
        self.assertEqual(item["action_label"], "删除内容")


if __name__ == "__main__":
    unittest.main()
