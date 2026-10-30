"""登录会话（登录设备）与单会话吊销的回归测试。

这套机制解决的是一个**只能靠测试守住**的问题：会话是 Redis 里的旁路状态，
踢没踢干净在运行时没有任何症状——界面上显示「已下线」，那台设备却还能继续刷新令牌。
所以这里把四条契约钉死：

1. 多设备登录互不覆盖（refresh 按 sid 隔离），否则后登录的会把先登录的踢掉；
2. 踢单个会话 = 删记录 + 删它自己的 refresh 键 + 从索引移除，**其它会话不受影响**；
3. 「退出其他设备」保留当前这台；「退出全部」一台不留；
4. 旧 token（无 sid）仍走老的「每用户一枚」键，升级不能把已登录用户踢下线。
"""

import unittest
from unittest.mock import patch

from app.core import session_store
from app.core.security import (
    create_token_pair,
    load_refresh_token,
    store_refresh_token,
)
from app.core.session_store import (
    create_session,
    describe_user_agent,
    list_sessions,
    new_session_id,
    revoke_all_sessions,
    revoke_other_sessions,
    revoke_session,
)
from tests.conftest import FakeRedis


class _SessionTestBase(unittest.IsolatedAsyncioTestCase):
    """把三处模块级 `redis_client` 都换成 FakeRedis。

    为什么要三处：`from app.redis import redis_client` 是**名字绑定**，
    security / auth_service / session_store 各自持有一份引用，
    只补丁其中一处，其余调用会打到真实 Redis（本地有 Redis 时表现为
    「测试偶发通过」，没 Redis 时表现为超时）。
    """

    def setUp(self):
        from app.core import security
        from app.services import auth_service

        self.fake = FakeRedis()
        self._patchers = [
            patch.object(security, "redis_client", self.fake),
            patch.object(auth_service, "redis_client", self.fake),
            patch.object(session_store, "redis_client", self.fake),
        ]
        for patcher in self._patchers:
            patcher.start()
            self.addCleanup(patcher.stop)


class SessionRegistryTests(_SessionTestBase):
    async def test_two_devices_coexist(self):
        sid_a, sid_b = new_session_id(), new_session_id()
        await create_session(1001, sid_a, "jti-a", "Mozilla/5.0 (Windows NT 10.0) Chrome/120", "10.0.0.1")
        await create_session(1001, sid_b, "jti-b", "Mozilla/5.0 (iPhone) Safari/604", "10.0.0.2")

        rows = await list_sessions(1001)
        self.assertEqual({row["sid"] for row in rows}, {sid_a, sid_b})
        devices = {row["device"] for row in rows}
        self.assertIn("Chrome · Windows", devices)
        self.assertIn("Safari · iOS", devices)

    async def test_list_is_scoped_to_user(self):
        sid_a, sid_b = new_session_id(), new_session_id()
        await create_session(1001, sid_a, "jti-a", "curl/8.0", "1.1.1.1")
        await create_session(2002, sid_b, "jti-b", "curl/8.0", "1.1.1.1")
        self.assertEqual([row["sid"] for row in await list_sessions(1001)], [sid_a])
        self.assertEqual([row["sid"] for row in await list_sessions(2002)], [sid_b])

    async def test_revoke_one_leaves_the_other(self):
        sid_a, sid_b = new_session_id(), new_session_id()
        await create_session(1001, sid_a, "jti-a", "curl/8.0", "1.1.1.1")
        await create_session(1001, sid_b, "jti-b", "curl/8.0", "1.1.1.1")
        await store_refresh_token(1001, "refresh-a", sid=sid_a)
        await store_refresh_token(1001, "refresh-b", sid=sid_b)

        record = await revoke_session(1001, sid_a)

        self.assertIsNotNone(record)
        self.assertEqual(record["access_jti"], "jti-a")
        self.assertEqual([row["sid"] for row in await list_sessions(1001)], [sid_b])
        # 被踢的那台再也续不了期，另一台不受影响
        self.assertIsNone(await load_refresh_token(1001, sid_a))
        self.assertEqual(await load_refresh_token(1001, sid_b), "refresh-b")

    async def test_revoke_foreign_session_is_rejected(self):
        """拿别人的 sid 来踢：必须当成「不存在」，不能越权"""
        sid = new_session_id()
        await create_session(1001, sid, "jti-a", "curl/8.0", "1.1.1.1")
        self.assertIsNone(await revoke_session(2002, sid))
        self.assertEqual(len(await list_sessions(1001)), 1)

    async def test_revoke_unknown_session_is_idempotent(self):
        self.assertIsNone(await revoke_session(1001, new_session_id()))

    async def test_revoke_other_sessions_keeps_current(self):
        sids = [new_session_id() for _ in range(3)]
        for sid in sids:
            await create_session(1001, sid, f"jti-{sid}", "curl/8.0", "1.1.1.1")

        revoked = await revoke_other_sessions(1001, keep_sid=sids[0])

        self.assertEqual(len(revoked), 2)
        self.assertEqual([row["sid"] for row in await list_sessions(1001)], [sids[0]])

    async def test_revoke_all_sessions(self):
        for _ in range(2):
            await create_session(1001, new_session_id(), "jti", "curl/8.0", "1.1.1.1")
        self.assertEqual(len(await revoke_all_sessions(1001)), 2)
        self.assertEqual(await list_sessions(1001), [])

    async def test_refresh_tokens_are_isolated_per_session(self):
        """多设备并存的核心：两枚 refresh 各存各的，谁刷新都只轮换自己那枚"""
        sid_a, sid_b = new_session_id(), new_session_id()
        await store_refresh_token(1001, "refresh-a", sid=sid_a)
        await store_refresh_token(1001, "refresh-b", sid=sid_b)
        self.assertEqual(await load_refresh_token(1001, sid_a), "refresh-a")
        self.assertEqual(await load_refresh_token(1001, sid_b), "refresh-b")

    async def test_legacy_refresh_key_still_works(self):
        """升级兼容：无 sid 的旧 token 仍读写「每用户一枚」的老键"""
        await store_refresh_token(1001, "legacy-token")
        self.assertEqual(await load_refresh_token(1001), "legacy-token")
        # 老键与新键互不干扰
        sid = new_session_id()
        await store_refresh_token(1001, "session-token", sid=sid)
        self.assertEqual(await load_refresh_token(1001), "legacy-token")
        self.assertEqual(await load_refresh_token(1001, sid), "session-token")


class SessionLogoutTests(_SessionTestBase):
    async def test_logout_only_revokes_current_session(self):
        from app.services import auth_service

        sid_a, sid_b = new_session_id(), new_session_id()
        await create_session(1001, sid_a, "jti-a", "curl/8.0", "1.1.1.1")
        await create_session(1001, sid_b, "jti-b", "curl/8.0", "1.1.1.1")
        await store_refresh_token(1001, "refresh-a", sid=sid_a)
        await store_refresh_token(1001, "refresh-b", sid=sid_b)

        access_a = create_token_pair(1001, "admin", sid=sid_a)["access_token"]
        await auth_service.logout(1001, access_token=access_a)

        # 当前会话被清掉，另一台还在（这就是「登出」与「退出全部设备」的区别）
        self.assertEqual([row["sid"] for row in await list_sessions(1001)], [sid_b])
        self.assertIsNone(await load_refresh_token(1001, sid_a))
        self.assertEqual(await load_refresh_token(1001, sid_b), "refresh-b")

    async def test_logout_all_clears_every_session(self):
        from app.services import auth_service

        for _ in range(2):
            sid = new_session_id()
            await create_session(1001, sid, "jti", "curl/8.0", "1.1.1.1")
            await store_refresh_token(1001, "refresh", sid=sid)

        await auth_service.logout_all(1001)

        self.assertEqual(await list_sessions(1001), [])


class UserAgentLabelTests(unittest.TestCase):
    def test_labels(self):
        cases = {
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36": "Chrome · Windows",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15": "Safari · macOS",
            "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0": "Firefox · Linux",
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36": "Chrome · Android",
            "curl/8.4.0": "命令行",
            "": "未知设备",
        }
        for ua, expected in cases.items():
            self.assertEqual(describe_user_agent(ua), expected, f"UA 解析不符: {ua!r}")


if __name__ == "__main__":
    unittest.main()
