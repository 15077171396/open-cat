"""危险操作二次校验（elevation）单元测试：签发 / 校验 / fail-closed。

Redis 全部走 FakeRedis 替身；密码哈希用真实 bcrypt（与登录侧同一算法）。
"""

import unittest
from unittest import mock

from app.core import elevation
from app.core.exceptions import AppException
from app.core.security import hash_password
from tests.conftest import FakeRedis


def _admin(password: str = "Sup3rSecret!"):
    from app.models.user import User

    return User(id=9, account="boss@example.com", password_hash=hash_password(password), role="admin")


class GrantTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_password_rejected(self):
        store = FakeRedis()
        with mock.patch.object(elevation, "redis_client", store):
            with self.assertRaises(AppException) as ctx:
                await elevation.grant(_admin(), "wrong-password")
            self.assertEqual(ctx.exception.code, 400)
        # 拒绝时不得留下任何已授权凭据
        self.assertEqual(await store.get("elevate:9"), None)

    async def test_correct_password_grants_token_with_ttl(self):
        store = FakeRedis()
        with mock.patch.object(elevation, "redis_client", store):
            result = await elevation.grant(_admin(), "Sup3rSecret!")
        self.assertTrue(result["token"])
        self.assertEqual(result["expires_in"], elevation._TTL_SECONDS)
        self.assertEqual(await store.get("elevate:9"), result["token"], "凭据必须进 Redis 单槽（TTL 自动过期）")

    async def test_new_grant_overwrites_previous_token(self):
        """单槽语义：最新一次验证覆盖上一次 —— 旧凭据立即失效。"""
        store = FakeRedis()
        with mock.patch.object(elevation, "redis_client", store):
            first = await elevation.grant(_admin(), "Sup3rSecret!")
            second = await elevation.grant(_admin(), "Sup3rSecret!")
            self.assertNotEqual(first["token"], second["token"])
            self.assertFalse(await elevation.verify(9, first["token"]), "旧凭据应被覆盖失效")
            self.assertTrue(await elevation.verify(9, second["token"]))


class VerifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_is_false(self):
        store = FakeRedis()
        with mock.patch.object(elevation, "redis_client", store):
            self.assertFalse(await elevation.verify(9, None))
            self.assertFalse(await elevation.verify(9, ""))

    async def test_mismatched_token_is_false(self):
        store = FakeRedis()
        await store.set("elevate:9", "real-token")
        with mock.patch.object(elevation, "redis_client", store):
            self.assertFalse(await elevation.verify(9, "forged-token"))
            self.assertTrue(await elevation.verify(9, "real-token"))

    async def test_redis_failure_is_fail_closed(self):
        """Redis 挂了按未授权处理 —— 危险操作宁可不让做。"""

        class _Boom:
            async def get(self, *_a, **_k):
                raise ConnectionError("redis down")

        with mock.patch.object(elevation, "redis_client", _Boom()):
            self.assertFalse(await elevation.verify(9, "anything"))


class RemainingSecondsTests(unittest.TestCase):
    def test_clamps_to_zero(self):
        self.assertEqual(elevation.remaining_seconds(1000, 995), 295)
        self.assertEqual(elevation.remaining_seconds(99999, 0), 0, "过期必须钳到 0，不能回吐负数")


if __name__ == "__main__":
    unittest.main()
