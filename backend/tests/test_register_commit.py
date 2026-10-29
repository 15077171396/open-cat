"""注册事务提交的回归测试：register 曾是唯一不显式 commit 的用户写路径，落库只靠 get_db 的
teardown commit（在响应发送之后才执行）——客户端已拿到 token 但 /auth/me 可能读不到未提交行，
或 teardown 未执行导致账号从未创建而 refresh_token 已写进 Redis。
本文件钉住「注册必须在返回 token 之前提交」。替身 flush 不区分 commit，故直接断言 commits 计数。"""

import unittest
from unittest import mock

from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.core import security as security_mod
from app.services import auth_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


async def _always_valid_code(account: str, code: str) -> bool:
    return True


async def _invite_disabled():
    """邀请功能关闭：本用例只关心提交语义，不引入邀请链路"""
    return False, 0


class RegisterCommitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        for patcher in (
            mock.patch.object(auth_service, "redis_client", FakeRedis()),
            # get_token_version / store_refresh_token 用的是 security 模块自己的
            # redis_client；不替换会去打真实 Redis（每次等 2s 连接超时，静默变慢）
            mock.patch.object(security_mod, "redis_client", FakeRedis()),
            mock.patch.object(auth_service, "verify_code", _always_valid_code),
            mock.patch.object(auth_service, "get_invite_settings", _invite_disabled),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _req(self) -> RegisterRequest:
        return RegisterRequest(
            account="newbie@example.com", password="pw123456", code="123456"
        )

    async def test_register_commits_before_issuing_token(self):
        result = await auth_service.register(self._req(), self.db)

        self.assertIn("access_token", result)
        self.assertGreaterEqual(
            self.db.commits, 1,
            "注册没有显式提交：账号落库只能等 get_db teardown，而它在响应发送之后执行",
        )
        rows = self.db.all_rows(User)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account"], "newbie@example.com")

    async def test_register_still_rejects_duplicate_account(self):
        """回归保护：加了提交不能破坏「重复账号 409」"""
        self.db.seed(
            User(id=1, account="newbie@example.com", password_hash="x", role="user")
        )
        from app.core.exceptions import AppException

        with self.assertRaises(AppException) as ctx:
            await auth_service.register(self._req(), self.db)
        self.assertEqual(ctx.exception.code, 409)


if __name__ == "__main__":
    unittest.main()
