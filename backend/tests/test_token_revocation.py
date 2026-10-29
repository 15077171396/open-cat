"""令牌撤销链路回归测试 (P1-6)。

台账 P1-6 登记的问题是「登出后 access_token 在 2 小时有效期内仍可调用接口」。
修复已在 ``app/core/security.py`` 落地（jti 黑名单 + 用户令牌版本号双机制），
但此前**没有任何测试覆盖**——这类安全机制最怕的就是某次重构悄悄把
``dependencies.py`` 里的校验摘掉而测试仍全绿。

本文件把三条契约钉死：
1. 登出（黑名单）→ 当前 token 立即失效，且**同用户其它会话 token 不受影响**；
2. 改密/换邮箱（版本号 INCR）→ 该用户**全部**存量 token 立即失效；
3. Redis 故障 → fail-open（降级为撤销前的可认证行为），**不因缓存不可用而阻断登录态**。

另外钉住两个易错细节：refresh token 不应被写入 access 黑名单；
旧版本签发的无 jti token 仍能靠版本号兜底吊销。
"""

import unittest

from app.core import security
from app.core.security import (
    blacklist_access_token,
    bump_token_version,
    create_access_token,
    create_refresh_token,
    decode_token,
    is_access_token_revoked,
)
from tests.conftest import FakeRedis


class _BrokenRedis:
    """任何命令都抛异常的 Redis：用于验证撤销校验的 fail-open 降级。"""

    def __getattr__(self, _name):
        def _raise(*args, **kwargs):
            raise RuntimeError("redis is down")
        return _raise

    def pipeline(self):
        raise RuntimeError("redis is down")


class LogoutByRefreshTokenTests(unittest.IsolatedAsyncioTestCase):
    """登出可以用 refresh token 作为凭据（第三轮增量审查 P1-36）。

    为什么需要：access token 过期后 `/auth/logout` 会 401，而前端把失败吞成
    `console.warn` —— 服务端吊销从未发生，7 天有效的 refresh token 仍可续期，
    「登出」构不成事件遏制。本文件钉住：仅凭 refresh token 也能吊销，
    且**不能**凭一枚「自己签发的 / 已被轮换掉的」token 吊销他人会话。
    """

    def setUp(self):
        from app.services import auth_service as auth_service_module

        self._orig_redis = security.redis_client
        self._orig_auth_redis = auth_service_module.redis_client
        fake = FakeRedis()
        security.redis_client = fake
        # auth_service.logout() 用的是**它自己模块里**的 redis_client 名字，
        # 只补丁 security.redis_client 的话，删除 refresh_token 会打到真实客户端，
        # FakeRedis 里那条记录仍在 → 「登出后必须删除」的断言必然失败。
        auth_service_module.redis_client = fake
        self.addCleanup(self._restore)

    def _restore(self):
        from app.services import auth_service as auth_service_module

        security.redis_client = self._orig_redis
        auth_service_module.redis_client = self._orig_auth_redis

    async def test_stored_refresh_token_resolves_user(self):
        from app.api.auth import _user_id_from_refresh_token

        uid = 9001
        refresh = create_refresh_token({"sub": str(uid), "role": "admin"})
        await security.store_refresh_token(uid, refresh)
        self.assertEqual(await _user_id_from_refresh_token(refresh), uid)

    async def test_unstored_token_is_rejected(self):
        """自己签发一枚、但未存进 Redis 的 refresh token 不得命中"""
        from app.api.auth import _user_id_from_refresh_token

        uid = 9002
        # ⚠️ refresh token 不含 jti：同一秒内相同 claims 会签出**逐字相同**的
        # 字符串，两枚「不同」的 token 实际是同一枚，断言就测不出任何东西。
        # 这里用不同的 claim 让二者真的不同，模拟「轮换后已失效的旧 token」。
        await security.store_refresh_token(
            uid, create_refresh_token({"sub": str(uid), "v": "new"})
        )
        stale = create_refresh_token({"sub": str(uid), "v": "old"})
        self.assertIsNone(await _user_id_from_refresh_token(stale))

    async def test_access_token_cannot_be_used_as_credential(self):
        from app.api.auth import _user_id_from_refresh_token

        uid = 9003
        self.assertIsNone(
            await _user_id_from_refresh_token(create_access_token({"sub": str(uid)}))
        )

    async def test_garbage_and_empty_are_rejected(self):
        from app.api.auth import _user_id_from_refresh_token

        for bad in ("", "not-a-token", "a.b.c"):
            with self.subTest(bad=bad):
                self.assertIsNone(await _user_id_from_refresh_token(bad))

    async def test_logout_endpoint_revokes_with_only_refresh_token(self):
        """端到端：不带 Authorization，仅 body 带 refresh token 也能吊销"""
        import httpx
        from fastapi import FastAPI

        from app.api.auth import router as auth_router
        from app.core.exceptions import register_exception_handlers
        from app.database import get_db
        from app.models.user import User
        from tests.fake_db import MemorySession

        uid = 9004
        db = MemorySession()
        db.seed(User(id=uid, account=f"u{uid}@example.com", password_hash="x", role="user"))

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(auth_router, prefix="/api/v1/auth")

        async def _override_db():
            yield db

        app.dependency_overrides[get_db] = _override_db

        refresh = create_refresh_token({"sub": str(uid), "role": "user"})
        await security.store_refresh_token(uid, refresh)
        self.assertIsNotNone(await security.load_refresh_token(uid))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/logout", json={"refresh_token": refresh}
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(
            await security.load_refresh_token(uid),
            "仅凭 refresh token 登出后，服务端的 refresh token 必须已被删除",
        )
        app.dependency_overrides.clear()

    async def test_logout_revokes_with_refresh_cookie(self):
        """主前端 cookie 模式：access cookie 已过期时仅凭 refresh cookie 也能吊销

        主前端用 httpOnly cookie（`credentials: 'include'`）登出，JS 读不到
        refresh token，只能靠 cookie 带到服务端。access cookie 过期时
        `get_current_user_optional` 返回 None —— 若不回退到 refresh cookie，
        吊销依旧不会发生，P1-36 指出的问题在原样保留。
        """
        import httpx
        from fastapi import FastAPI

        from app.api.auth import router as auth_router
        from app.core.exceptions import register_exception_handlers
        from app.core.security import REFRESH_TOKEN_COOKIE

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(auth_router, prefix="/api/v1/auth")

        uid = 9005
        refresh = create_refresh_token({"sub": str(uid), "role": "user"})
        await security.store_refresh_token(uid, refresh)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={REFRESH_TOKEN_COOKIE: refresh},
        ) as client:
            resp = await client.post("/api/v1/auth/logout")

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(
            await security.load_refresh_token(uid),
            "仅凭 refresh cookie 登出后，服务端的 refresh token 必须已被删除",
        )

    async def test_logout_without_any_credential_still_returns_200(self):
        """登出是幂等的：没有任何凭据也返回 200（刻意不返回 401，避免探测面）"""
        import httpx
        from fastapi import FastAPI

        from app.api.auth import router as auth_router
        from app.core.exceptions import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(auth_router, prefix="/api/v1/auth")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/auth/logout", json={})
        self.assertEqual(resp.status_code, 200)


class TokenRevocationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_redis = security.redis_client
        security.redis_client = FakeRedis()

    def tearDown(self):
        security.redis_client = self._orig_redis

    async def test_token_is_valid_before_logout(self):
        token = create_access_token({"sub": "1001", "role": "user"})
        self.assertFalse(await is_access_token_revoked(decode_token(token)))

    async def test_logout_invalidates_current_token(self):
        """登出后当前 token 必须立刻失效（否则 120 分钟内仍可调用所有接口）"""
        token = create_access_token({"sub": "1001", "role": "user"})
        await blacklist_access_token(token)

        self.assertTrue(await is_access_token_revoked(decode_token(token)))

    async def test_logout_does_not_invalidate_other_sessions(self):
        """黑名单是「单会话吊销」：同一用户的其它 token 不能受牵连"""
        token_a = create_access_token({"sub": "1001", "role": "user"})
        token_b = create_access_token({"sub": "1001", "role": "user"})
        await blacklist_access_token(token_a)

        self.assertTrue(await is_access_token_revoked(decode_token(token_a)))
        self.assertFalse(await is_access_token_revoked(decode_token(token_b)))

    async def test_version_bump_invalidates_all_tokens_of_user(self):
        """改密/换邮箱：该用户全部存量 token 立即失效"""
        old_token = create_access_token({"sub": "1002", "role": "user"}, ver=0)
        await bump_token_version(1002)

        self.assertTrue(await is_access_token_revoked(decode_token(old_token)))

        # 版本号提升后新签发的 token 必须可用，否则等于永久登出
        new_token = create_access_token({"sub": "1002", "role": "user"}, ver=1)
        self.assertFalse(await is_access_token_revoked(decode_token(new_token)))

    async def test_version_bump_is_scoped_to_single_user(self):
        await bump_token_version(1003)
        other = create_access_token({"sub": "9999", "role": "user"}, ver=0)
        self.assertFalse(await is_access_token_revoked(decode_token(other)))

    async def test_refresh_token_is_not_blacklisted(self):
        """refresh token 不进 access 黑名单（类型判定错误会误伤刷新链路）"""
        refresh = create_refresh_token({"sub": "1004", "role": "user"})
        await blacklist_access_token(refresh)

        self.assertEqual(security.redis_client._store, {})

    async def test_legacy_token_without_jti_still_revocable_by_version(self):
        """升级过渡期签发的无 jti token：单会话吊销不可用，但版本号仍生效"""
        payload = {"sub": "1005", "role": "user", "type": "access", "ver": 0}
        token = security.jwt.encode(payload, security.settings.SECRET_KEY,
                                    algorithm=security.settings.JWT_ALGORITHM)
        await blacklist_access_token(token)
        self.assertFalse(await is_access_token_revoked(decode_token(token)))

        await bump_token_version(1005)
        self.assertTrue(await is_access_token_revoked(decode_token(token)))

    async def test_redis_outage_fails_open(self):
        """Redis 不可用时不能把所有已登录用户踢下线（降级为撤销前的行为）"""
        security.redis_client = _BrokenRedis()
        token = create_access_token({"sub": "1006", "role": "user"})

        self.assertFalse(await is_access_token_revoked(decode_token(token)))
        # 写路径同样只记日志、不抛异常，否则登出接口会 500
        await blacklist_access_token(token)
        await bump_token_version(1006)
