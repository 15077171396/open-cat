"""会话有效期策略契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **绝对存活上限是硬闸门**：refresh 链超过 SESSION_MAX_DAYS 一律拒绝并
   **吊销会话** —— 防「7 天内点一次就永远不用重新登录」的无限续期链；
   被盗号的 refresh_token 也会因此自然失效。
2. **无会话簿的旧 token 跳过检查**：绝对上限依赖 sess:{sid} 的 created_at，
   旧客户端 token 无 sid/会话记录时跳过（不能因为兼容路径把人全部踢下线）。
3. **TTL 改动只影响新签发**：create_token_pair 读运行期配置，存量 token
   按签发时的 exp 自然过期 —— 「改小立即踢人」的语义要靠吊销而不是改 TTL。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.config import settings
from app.core.exceptions import AuthenticationError
from app.services import auth_service


def _run(coro):
    return asyncio.run(coro)


def _fake_db(user_row):
    """假会话：scalar_one_or_none 返回预设用户行"""
    return SimpleNamespace(
        execute=mock.AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: user_row)
        ),
        commit=mock.AsyncMock(),
        rollback=mock.AsyncMock(),
    )


class _FakeUser:
    def __init__(self):
        self.id = 42
        self.account = "u1@example.com"
        self.role = "user"
        self.deleted_at = None


class AbsoluteExpiryTests(unittest.TestCase):
    """refresh 流程的绝对存活闸门（auth_service.refresh_token 内联检查）"""

    def _refresh_with(self, *, session_created_at: int | None, has_sid: bool = True):
        """把 refresh_token 的全部外呼 mock 掉，只观察绝对过期闸门"""
        import time

        user = _FakeUser()
        db = _fake_db(user)

        decode_payload = (
            {"type": "refresh", "sub": "42", "sid": "s1", "jti": "j1"}
            if has_sid
            else {"type": "refresh", "sub": "42"}
        )
        patchers = [
            # decode_token 是同步函数：MagicMock 构造时即绑定返回值（事后改 .new 是 sentinel，无效）
            mock.patch.object(auth_service, "decode_token", new=mock.MagicMock(return_value=decode_payload)),
            mock.patch.object(auth_service, "load_refresh_token", new=mock.AsyncMock(return_value="rt")),
            mock.patch.object(auth_service, "get_token_version", new=mock.AsyncMock(return_value=1)),
            # create_token_pair 是同步函数（构造 JWT）：用 MagicMock 而不是 AsyncMock
            mock.patch.object(
                auth_service,
                "create_token_pair",
                new=mock.MagicMock(return_value={"access_token": "a", "refresh_token": "r"}),
            ),
            mock.patch.object(auth_service, "store_refresh_token", new=mock.AsyncMock()),
            mock.patch.object(auth_service, "touch_session", new=mock.AsyncMock()),
        ]

        session_obj = {"created_at": session_created_at} if session_created_at is not None else None
        get_session = mock.AsyncMock(return_value=session_obj)
        revoke = mock.AsyncMock()

        now = time.time()
        return patchers, get_session, revoke, db, user, now


class AbsoluteExpiryBodies(AbsoluteExpiryTests):
    def _run_refresh(self, patchers, get_session, revoke, db, max_days: int = 30):
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5]:
            with mock.patch.object(auth_service, "get_session", get_session):
                with mock.patch.object(auth_service, "revoke_session", revoke):
                    with mock.patch.object(settings, "SESSION_MAX_DAYS", max_days):
                        return _run(auth_service.refresh_token("rt", db))

    def test_over_limit_refresh_is_rejected_and_session_revoked(self):
        import time


        patchers, get_session, revoke, db, _, _ = self._refresh_with(
            session_created_at=int(time.time() - 60 * 86400)  # 60 天前创建
        )
        with self.assertRaises(AuthenticationError) as ctx:
            self._run_refresh(patchers, get_session, revoke, db)
        self.assertIn("重新登录", ctx.exception.message)
        revoke.assert_awaited_once()

    def test_within_limit_refresh_succeeds(self):
        import time


        patchers, get_session, revoke, db, _, _ = self._refresh_with(
            session_created_at=int(time.time() - 3 * 86400)  # 3 天前创建
        )
        tokens = self._run_refresh(patchers, get_session, revoke, db)
        self.assertIn("access_token", tokens)
        revoke.assert_not_awaited()

    def test_legacy_token_without_session_skips_check(self):
        """旧 token 无 sid：无会话簿记录 → 跳过绝对检查（兼容路径）"""
        import time


        patchers, get_session, revoke, db, _, _ = self._refresh_with(
            session_created_at=int(time.time() - 60 * 86400), has_sid=False
        )
        tokens = self._run_refresh(patchers, get_session, revoke, db)
        self.assertIn("access_token", tokens)
        revoke.assert_not_awaited()

    def test_zero_max_days_disables_gate(self):
        import time

        patchers, get_session, revoke, db, _, _ = self._refresh_with(
            session_created_at=int(time.time() - 60 * 86400)
        )
        with mock.patch.object(settings, "SESSION_MAX_DAYS", 0):
            tokens = self._run_refresh(patchers, get_session, revoke, db, max_days=0)
        self.assertIn("access_token", tokens)
        revoke.assert_not_awaited()


class TtlRuntimeTests(unittest.TestCase):
    def test_create_access_token_reads_runtime_setting(self):
        """TTL 走运行期配置：patch settings 即模拟后台保存后的生效形态"""
        from app.core.security import create_access_token, decode_token

        with mock.patch.object(settings, "JWT_ACCESS_EXPIRE_MINUTES", 10):
            token = create_access_token({"sub": "42"})
        payload = decode_token(token)
        self.assertIsNotNone(payload)
        # 10 分钟 ≈ 600 秒：exp 与签发时间差应在 [590, 610] 秒（容忍时钟粒度）
        from datetime import datetime, timezone

        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        drift = (exp - now).total_seconds()
        self.assertLessEqual(drift, 610)
        self.assertGreaterEqual(drift, 590)


if __name__ == "__main__":
    unittest.main()
