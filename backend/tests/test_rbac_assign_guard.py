"""RBAC 用户角色分配的安全约束单测：assign_user_rbac 曾无 operator 参数、不禁止自我提权、
不保护 super_admin 角色、零审计——而 super_admin 是一票放行的逃逸通道，持 user:edit 即可
一步变成全站最高权限。本文件钉住三条约束 + 一条正常流程回归。
替身说明：MemorySession 不实现 JOIN，故直接对 get_user_rbac 打桩注入「操作者是否 super_admin」。"""

import unittest
from unittest import mock

from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.services import rbac_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def _user(uid: int, role: str = "admin") -> User:
    return User(id=uid, account=f"u{uid}@example.com", password_hash="x", role=role)


def _role(rid: int, code: str) -> Role:
    return Role(id=rid, code=code, name=code, is_system=True)


class AssignUserRbacGuardTests(unittest.IsolatedAsyncioTestCase):
    """assign_user_rbac 的防提权约束"""

    def setUp(self):
        self.db = MemorySession()
        self._patch_redis = mock.patch.object(rbac_service, "redis_client", FakeRedis())
        self._patch_redis.start()
        self.addCleanup(self._patch_redis.stop)

    def _as_super_admin(self, is_super: bool):
        """打桩 get_user_rbac：控制「操作者是否持有 super_admin 角色」"""

        async def _fake_get_user_rbac(db, user_id):
            roles = [{"id": 1, "code": "super_admin", "name": "超级管理员"}] if is_super else []
            return {"roles": roles, "perms": []}

        return mock.patch.object(rbac_service, "get_user_rbac", _fake_get_user_rbac)

    # ---------- 约束 1：禁止自我提权 ----------

    async def test_cannot_assign_roles_to_self(self):
        """管理员不能变更自己的角色（否则 user:edit → super_admin 一步登天）"""
        admin = _user(1)
        self.db.seed(admin, _role(10, "editor"))

        with self.assertRaises(AppException) as ctx:
            await rbac_service.assign_user_rbac(self.db, 1, admin, role_ids=[10])

        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("自己", ctx.exception.message)
        # 拒绝时不应留下任何角色关联
        self.assertEqual(self.db.all_rows(UserRole), [])

    # ---------- 约束 2：非 super_admin 不得分配 super_admin ----------

    async def test_non_super_admin_cannot_grant_super_admin(self):
        """持有 user:edit 的普通管理员不能把 super_admin 授予任何人（含同伙）"""
        admin = _user(1)
        target = _user(2)
        self.db.seed(admin, target, _role(99, "super_admin"))

        with self._as_super_admin(False):
            with self.assertRaises(AppException) as ctx:
                await rbac_service.assign_user_rbac(self.db, 2, admin, role_ids=[99])

        self.assertEqual(ctx.exception.code, 403)
        self.assertIn("超级管理员", ctx.exception.message)
        self.assertEqual(self.db.all_rows(UserRole), [])

    async def test_non_super_admin_cannot_grant_super_admin_mixed_with_others(self):
        """混在多个角色里夹带 super_admin 也要被拦（不能只看 role_ids[0]）"""
        admin = _user(1)
        target = _user(2)
        self.db.seed(admin, target, _role(10, "editor"), _role(99, "super_admin"))

        with self._as_super_admin(False):
            with self.assertRaises(AppException) as ctx:
                await rbac_service.assign_user_rbac(self.db, 2, admin, role_ids=[10, 99])

        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(self.db.all_rows(UserRole), [])

    # ---------- 约束 3：正常路径仍可用（防误伤） ----------

    async def test_super_admin_can_grant_super_admin(self):
        """super_admin 分配 super_admin 是正常管理行为，必须放行"""
        admin = _user(1)
        target = _user(2)
        self.db.seed(admin, target, _role(99, "super_admin"))

        with self._as_super_admin(True):
            result = await rbac_service.assign_user_rbac(self.db, 2, admin, role_ids=[99])

        self.assertEqual(result["user_id"], 2)
        rows = self.db.all_rows(UserRole)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], 2)
        self.assertEqual(rows[0]["role_id"], 99)

    async def test_normal_role_assignment_still_works(self):
        """分配普通角色不受影响，且必须留下审计（此前完全没有）"""
        admin = _user(1)
        target = _user(2)
        self.db.seed(admin, target, _role(10, "editor"))

        with self._as_super_admin(False):
            result = await rbac_service.assign_user_rbac(self.db, 2, admin, role_ids=[10])

        self.assertEqual(result["user_id"], 2)
        self.assertEqual(len(self.db.all_rows(UserRole)), 1)

        audits = self.db.all_rows(AdminAuditLog)
        self.assertEqual(len(audits), 1, "角色分配必须写入管理操作审计")
        self.assertEqual(audits[0]["action"], "user_rbac_assign")
        self.assertEqual(audits[0]["target_id"], 2)
        self.assertEqual(audits[0]["admin_id"], 1)

    async def test_invalid_role_id_rejected(self):
        """引用不存在的角色 ID 仍按原逻辑拒绝"""
        admin = _user(1)
        target = _user(2)
        self.db.seed(admin, target)  # 故意不 seed 任何角色

        with self.assertRaises(AppException) as ctx:
            await rbac_service.assign_user_rbac(self.db, 2, admin, role_ids=[999])

        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("无效的角色", ctx.exception.message)

    async def test_clearing_roles_is_allowed(self):
        """清空他人角色（role_ids=[]）是合法管理操作，不应被 super_admin 检查误伤"""
        admin = _user(1)
        target = _user(2)
        self.db.seed(admin, target, _role(10, "editor"), UserRole(user_id=2, role_id=10))

        with self._as_super_admin(False):
            result = await rbac_service.assign_user_rbac(self.db, 2, admin, role_ids=[])

        self.assertEqual(result["user_id"], 2)
        self.assertEqual(self.db.all_rows(UserRole), [])


class _StubResult:
    """只回答 set_role_permissions 前置校验用到的两种结果形状。"""

    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _RoleOnlySession:
    def __init__(self, role: Role):
        self.role = role

    async def execute(self, stmt):
        if "FROM roles" in str(stmt):
            return _StubResult(self.role)
        return _StubResult(None)


class SetRolePermissionsRegressionTests(unittest.IsolatedAsyncioTestCase):
    """权限更新曾在 holds_role 查询里引用不存在的 UserRole.id，任何权限编辑都 500。

    UserRole 是 `(user_id, role_id)` 复合主键，没有 id 列；这里用一个只实现
    「取角色 + 查是否持有」的桩会话，确保该前置校验能正常跑完。
    """

    async def test_precheck_does_not_reference_missing_userrole_id(self):
        role = Role(id=10, code="editor", name="编辑", is_system=False)
        admin = _user(1)

        async def _no_perms(_db, _role_id):
            return []

        with mock.patch.object(rbac_service, "get_role_permissions", _no_perms):
            result = await rbac_service.set_role_permissions(
                _RoleOnlySession(role), 10, [], admin
            )

        self.assertEqual(result["id"], 10)
        self.assertEqual(result["permissions"], [])


if __name__ == "__main__":
    unittest.main()
