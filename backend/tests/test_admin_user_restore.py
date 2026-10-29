"""用户「已注销」视图与恢复接口的回归测试。

背景：用户自助注销与管理员删除**都是软删除**（`users.deleted_at`），但管理端此前
只有 `deleted_at IS NULL` 一条查询路径 —— 已注销账号在后台彻底消失，也没有任何恢复入口，
「软删除」实际上只剩「数据没物理删」的意义，误删无法挽回。

这里钉住三件事：
1. 列表能按 `account_status` 查到已注销账号，且**默认仍只返回正常账号**（不改变既有语义）；
2. 恢复只清 `deleted_at`，**不触碰积分 / 角色 / VIP**（「原样恢复」的语义边界）；
3. 权限码、路由规则、审计动作三处登记齐全 —— 漏任何一处都是「上线即 403 / 审计缺行」。

替身说明：`MemorySession` 不支持两实体 JOIN，而 `list_users` 内部会批量查 RBAC 角色，
故把 `_batch_rbac_info` 打桩成空（本文件不关心那几列）。
"""

import unittest
from datetime import datetime
from unittest import mock

from app.core import rbac as rbac_core
from app.core.exceptions import AppException
from app.models.admin_audit_log import AUDIT_ACTION_RISK, AUDIT_ACTIONS, AdminAuditLog
from app.models.user import User
from app.services import admin_user_service
from app.services.admin_user_service import list_users, restore_user
from app.services.rbac_catalog import ALL_PERMISSION_CODES
from tests.fake_db import MemorySession

DELETED_AT = datetime(2026, 10, 20, 3, 0, 0)


def _admin(uid: int = 1) -> User:
    return User(id=uid, account="admin@example.com", password_hash="x", role="admin")


def _user(
    uid: int,
    account: str,
    *,
    deleted_at: datetime | None = None,
    points: int = 0,
    role: str = "user",
) -> User:
    user = User(id=uid, account=account, password_hash="x", role=role, points=points)
    user.deleted_at = deleted_at
    return user


async def _no_rbac(db, user_ids):
    """RBAC 批量查询打桩：MemorySession 不实现 `select(A, B.col).join(...)`"""
    return {uid: [] for uid in user_ids}, {}


class ListUsersAccountStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        self.db.seed(
            _user(1, "active@example.com"),
            _user(2, "gone@example.com", deleted_at=DELETED_AT),
        )

    async def _list(self, **kwargs) -> dict:
        with mock.patch.object(admin_user_service, "_batch_rbac_info", _no_rbac):
            return await list_users(self.db, 1, 20, **kwargs)

    async def test_default_only_returns_active_accounts(self):
        """不传 account_status 时行为与改造前一致（回归保护）"""
        result = await self._list()
        self.assertEqual([i["account"] for i in result["items"]], ["active@example.com"])
        self.assertEqual(result["total"], 1)
        self.assertIsNone(result["items"][0]["deleted_at"])

    async def test_deleted_view_returns_only_cancelled_accounts(self):
        result = await self._list(account_status="deleted")
        self.assertEqual([i["account"] for i in result["items"]], ["gone@example.com"])
        self.assertIsNotNone(result["items"][0]["deleted_at"], "已注销视图必须带注销时间")

    async def test_all_view_marks_each_row(self):
        result = await self._list(account_status="all")
        self.assertEqual(result["total"], 2)
        by_account = {i["account"]: i for i in result["items"]}
        self.assertIsNone(by_account["active@example.com"]["deleted_at"])
        self.assertIsNotNone(by_account["gone@example.com"]["deleted_at"])

    async def test_keyword_filter_still_applies_in_deleted_view(self):
        result = await self._list(account_status="deleted", keyword="gone")
        self.assertEqual(len(result["items"]), 1)
        result = await self._list(account_status="deleted", keyword="active")
        self.assertEqual(result["items"], [])


class RestoreUserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        self.admin = _admin()
        self.db.seed(self.admin)

    def _row(self, user_id: int) -> dict:
        return next(r for r in self.db.all_rows(User) if r["id"] == user_id)

    async def test_restore_clears_flag_and_keeps_data(self):
        """原样恢复：只清 deleted_at，积分/角色/VIP 一概不动"""
        self.db.seed(
            _user(7, "back@example.com", deleted_at=DELETED_AT, points=120, role="vip")
        )
        result = await restore_user(self.db, self.admin, 7)

        self.assertEqual(result["account"], "back@example.com")
        row = self._row(7)
        self.assertIsNone(row["deleted_at"])
        self.assertEqual(row["points"], 120, "恢复不能顺手清积分")
        self.assertEqual(row["role"], "vip", "恢复不能降级原 VIP 用户")
        self.assertEqual(self.db.commits, 1)

    async def test_restore_writes_audit_row(self):
        self.db.seed(_user(7, "back@example.com", deleted_at=DELETED_AT))
        await restore_user(self.db, self.admin, 7)
        actions = [row["action"] for row in self.db.all_rows(AdminAuditLog)]
        self.assertIn("user_restore", actions, "恢复账号属高危动作，必须留审计")

    async def test_restore_active_user_is_rejected(self):
        """正常账号无需恢复：404 而不是静默成功（避免误以为「刚恢复过」）"""
        self.db.seed(_user(8, "live@example.com"))
        with self.assertRaises(AppException) as ctx:
            await restore_user(self.db, self.admin, 8)
        self.assertEqual(ctx.exception.code, 404)

    async def test_restore_missing_user_is_rejected(self):
        with self.assertRaises(AppException) as ctx:
            await restore_user(self.db, self.admin, 999)
        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(self.db.commits, 0)


class CreateUserAgainstDeletedAccountTests(unittest.IsolatedAsyncioTestCase):
    """管理员建号撞到已注销账号：不能只回一句「不能复用」

    前端要靠 `data.reason` 把 409 分流成「换个账号」与「去已注销列表恢复」两件事，
    所以判别值必须是**结构化字段**而不是 message 文本（文案一改就静默失效）。
    """

    def setUp(self):
        self.db = MemorySession()
        self.admin = _admin()
        self.db.seed(self.admin)

    async def test_deleted_account_returns_structured_reason(self):
        from app.services.admin_user_service import create_user

        self.db.seed(_user(9, "gone@example.com", deleted_at=DELETED_AT))
        with self.assertRaises(AppException) as ctx:
            await create_user(
                self.db, self.admin, {"account": "gone@example.com", "password": "pw123456"}
            )
        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(ctx.exception.data["reason"], "account_deleted")
        self.assertEqual(ctx.exception.data["user_id"], 9)
        self.assertEqual(ctx.exception.data["account"], "gone@example.com")

    async def test_active_account_conflict_has_no_reason(self):
        """正常重名不该被前端当成「可恢复」——否则会引导用户去一个查不到的列表"""
        from app.services.admin_user_service import create_user

        self.db.seed(_user(10, "live@example.com"))
        with self.assertRaises(AppException) as ctx:
            await create_user(
                self.db, self.admin, {"account": "live@example.com", "password": "pw123456"}
            )
        self.assertEqual(ctx.exception.code, 409)
        self.assertIsNone(ctx.exception.data)


class RestoreWiringTests(unittest.TestCase):
    """三处登记缺一不可：权限码（否则 403）、路由规则（fail-closed）、审计动作（否则无痕）"""

    def test_permission_code_registered(self):
        self.assertIn("user:restore", ALL_PERMISSION_CODES)

    def test_route_rule_registered(self):
        self.assertEqual(
            rbac_core.match_route_permission("POST", "/api/v1/admin/users/123/restore"),
            ["user:restore"],
        )

    def test_audit_action_registered_with_danger_risk(self):
        self.assertIn("user_restore", AUDIT_ACTIONS)
        self.assertEqual(
            AUDIT_ACTION_RISK["user_restore"],
            "danger",
            "恢复已处置账号 = 归还访问权，风险等级必须与删除同级",
        )

    def test_list_endpoint_restricts_account_status_values(self):
        """接口层用 Literal 校验取值：非法值 422，而不是静默降级成「全部」"""
        import inspect
        from typing import get_args

        from app.api.admin_users import admin_list_users

        sig = inspect.signature(admin_list_users)
        self.assertIn("account_status", sig.parameters)
        allowed = set(get_args(sig.parameters["account_status"].annotation))
        self.assertEqual(allowed, {"active", "deleted", "all"})


if __name__ == "__main__":
    unittest.main()
