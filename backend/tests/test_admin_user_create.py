"""管理员建号（含 RBAC 分配）的回归测试：assign_user_rbac 曾新增必填 admin 参数而漏改
admin_users.py 调用点，运行期 TypeError 使带 RBAC 字段的建号必 500、且账号已落库成僵尸账号。
钉住两件事：① 带 RBAC 字段建号成功且两条审计落库；② 建号与 RBAC 分配同事务（RBAC 失败不得提交）。
替身说明：MemorySession.rollback 不回滚已 flush 的行，故断言「没有发生 commit」而非「行被回滚」。"""

import unittest
from unittest import mock

from app.api.admin_users import UserCreateRequest, admin_create_user
from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.rbac import Role
from app.models.user import User
from app.services import rbac_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def _admin(uid: int = 1) -> User:
    return User(id=uid, account=f"admin{uid}@example.com", password_hash="x", role="admin")


def _role(rid: int, code: str) -> Role:
    return Role(id=rid, code=code, name=code, is_system=True)


class AdminCreateUserWithRbacTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        self.admin = _admin()
        self.db.seed(self.admin, _role(10, "editor"))
        # 权限缓存走 Redis：必须换 FakeRedis，否则每次调用都等真实连接的
        # socket_connect_timeout（2s），套件会静默变慢且不再是离线用例
        self._patch_redis = mock.patch.object(rbac_service, "redis_client", FakeRedis())
        self._patch_redis.start()
        self.addCleanup(self._patch_redis.stop)

    def _req(self, **overrides) -> UserCreateRequest:
        payload = {
            "account": "newbie@example.com",
            "password": "pw123456",
            "role": "user",
        }
        payload.update(overrides)
        return UserCreateRequest(**payload)

    async def test_rbac_branch_succeeds_and_writes_both_audits(self):
        """回归：带 rbac_role_ids 建号必须成功（修复前是 TypeError → 500）"""
        result = await admin_create_user(
            self._req(rbac_role_ids=[10]), db=self.db, admin=self.admin
        )

        self.assertEqual(result["code"], 0)
        actions = [row["action"] for row in self.db.all_rows(AdminAuditLog)]
        self.assertIn("user_create", actions)
        self.assertIn(
            "user_rbac_assign", actions,
            "RBAC 分配没写审计：事务可能被提前提交或分配步骤被跳过",
        )
        # 注：不在此断言提交次数 —— assign_user_rbac 内部自带一次 commit（:516），
        # 路由层再提交一次，次数是实现细节；真正要钉的是下面那条「失败不得提交」。
        self.assertGreaterEqual(self.db.commits, 1)

    async def test_rbac_failure_does_not_commit_user_creation(self):
        """原子性：RBAC 分配失败时整笔不得提交（修复前 create_user 已先 commit）"""
        with self.assertRaises(AppException):
            await admin_create_user(
                self._req(rbac_role_ids=[999]), db=self.db, admin=self.admin
            )
        self.assertEqual(
            self.db.commits, 0,
            "RBAC 分配失败却已提交，会在库里留下「账号已建、角色未分配」的僵尸账号",
        )

    async def test_plain_create_still_commits_once(self):
        """不带 RBAC 字段的普通建号路径不受影响（仍自洽提交一次）"""
        result = await admin_create_user(self._req(), db=self.db, admin=self.admin)
        self.assertEqual(result["code"], 0)
        self.assertEqual(self.db.commits, 1)


class CreateAdminRoleGuardTests(unittest.IsolatedAsyncioTestCase):
    """创建 admin 角色账号的权限门槛（第三轮增量审查 P1-47）

    `POST /admin/users` 只由 `user:create` 把关，而把**已有**用户升级为 admin 走的是
    `user:edit` —— 两扇门只锁了一扇。这里钉住：创建 admin 角色账号同样需要 `user:edit`。
    内置的 content_admin / viewer 都没有 user:create，super_admin 一票放行，
    所以只有「自定义角色只给了 user:create」这一种配置会受影响。
    """

    def setUp(self):
        self.db = MemorySession()
        self.admin = _admin()
        self.db.seed(self.admin)

    def _as_operator(self, *, is_super: bool, perms: list[str]):
        """打桩 get_user_rbac：只注入「操作者持有什么」这一个事实。

        不走真实 seed：`get_user_rbac` 的库查询依赖 JOIN，而 MemorySession 不实现 JOIN。
        """

        async def _fake_get_user_rbac(db, user_id):
            roles = [{"id": 1, "code": "super_admin", "name": "超管"}] if is_super else []
            return {"roles": roles, "perms": perms}

        return mock.patch.object(rbac_service, "get_user_rbac", _fake_get_user_rbac)

    def _req(self, **overrides) -> UserCreateRequest:
        payload = {
            "account": "newadmin@example.com",
            "password": "pw123456",
            "role": "admin",
        }
        payload.update(overrides)
        return UserCreateRequest(**payload)

    async def test_creating_admin_role_requires_user_edit(self):
        """只有 user:create 的运营不能造管理员账号"""
        with self._as_operator(is_super=False, perms=["user:create"]):
            with self.assertRaises(AppException) as ctx:
                await admin_create_user(self._req(), db=self.db, admin=self.admin)
        self.assertEqual(ctx.exception.code, 403)

    async def test_super_admin_can_create_admin_role(self):
        """超级管理员不受影响（一票放行）"""
        with self._as_operator(is_super=True, perms=[]):
            result = await admin_create_user(self._req(), db=self.db, admin=self.admin)
        self.assertEqual(result["code"], 0)

    async def test_operator_with_user_edit_can_create_admin_role(self):
        """同时持有 user:edit 的自定义角色可以创建（不误伤）"""
        with self._as_operator(is_super=False, perms=["user:create", "user:edit"]):
            result = await admin_create_user(self._req(), db=self.db, admin=self.admin)
        self.assertEqual(result["code"], 0)

    async def test_creating_normal_role_needs_no_user_edit(self):
        """创建普通/vip 用户不受该门槛影响（否则会误伤日常建号）"""
        with self._as_operator(is_super=False, perms=["user:create"]):
            result = await admin_create_user(
                self._req(role="user"), db=self.db, admin=self.admin
            )
        self.assertEqual(result["code"], 0)


class LoginAccountLengthTests(unittest.TestCase):
    """登录账号必须有长度上限（第三轮增量审查 P1-35）

    `_record_login_failure` 用**原始 account 字符串**写 Redis 键；无上限时
    `{"account": "<20MB>"}` 可把 Redis 写满，而 `noeviction` 下写满会让
    `is_access_token_revoked` 的 fail-open 生效 —— 登出/改密/降权静默失效。
    上限取 100 与 DB 列 `String(100)` 对齐，不会挡住任何存量账号。
    """

    def test_overlong_account_rejected(self):
        from pydantic import ValidationError

        from app.schemas.auth import LoginRequest

        with self.assertRaises(ValidationError):
            LoginRequest(account="a" * 101, password="pw")

    def test_empty_account_rejected(self):
        from pydantic import ValidationError

        from app.schemas.auth import LoginRequest

        with self.assertRaises(ValidationError):
            LoginRequest(account="", password="pw")

    def test_normal_accounts_still_accepted(self):
        from app.schemas.auth import LoginRequest

        for account in ("13800138000", "user@example.com", "a" * 100):
            with self.subTest(account=account[:12]):
                self.assertEqual(LoginRequest(account=account, password="pw").account, account)


if __name__ == "__main__":
    unittest.main()
