"""界面偏好 / 锁屏口令：Schema 白名单、解锁令牌、限流与接口行为。

重点钉住四条不变量：
1. 偏好只落白名单字段，未知字段静默丢弃、非法类型 422；
2. 解锁令牌与 access/refresh 隔离（不能被当成登录令牌复用）；
3. 错误口令按登录限流同口径累计，达上限返回 429；
4. 口令明文永远不出现在响应与审计 detail 里。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from pydantic import ValidationError

from app.core import rbac
from app.core.exceptions import AppException
from app.core.security import create_access_token, create_unlock_token, decode_unlock_token
from app.schemas.ui_preferences import UiPreferences
from app.services import auth_service, ui_preferences_service
from tests.conftest import FakeRedis, async_client, make_user


class PreferenceSchemaTests(unittest.TestCase):
    def test_unknown_fields_are_ignored(self):
        model = UiPreferences.model_validate({"menu_width": 250, "evil": "drop table"})
        self.assertEqual(model.only_set_fields(), {"menu_width": 250})

    def test_invalid_enum_and_number_rejected(self):
        for payload in (
            {"mode": "auto"},
            {"menu_width": 233},  # 演示站步长 10
            {"menu_width": 500},
            {"primary_color": "red"},
            {"radius": 0.6},
        ):
            with self.assertRaises(ValidationError):
                UiPreferences.model_validate(payload)

    def test_merge_keeps_unchanged_and_filters_unknown(self):
        merged = ui_preferences_service.merge_preferences(
            {"mode": "dark", "show_tabs": False},
            {"menu_width": 250, "unknown": "x"},
        )
        self.assertEqual(merged["mode"], "dark")
        self.assertFalse(merged["show_tabs"])
        self.assertEqual(merged["menu_width"], 250)
        self.assertNotIn("unknown", merged)

    def test_sanitize_falls_back_per_field(self):
        result = ui_preferences_service.sanitize_preferences(
            {"mode": "dark", "menu_width": "bad", "unknown": 1}
        )
        self.assertEqual(result["mode"], "dark")
        self.assertEqual(result["menu_width"], 230)
        self.assertNotIn("unknown", result)


class UnlockTokenTests(unittest.TestCase):
    def test_roundtrip_and_scope_isolation(self):
        token = create_unlock_token(7)
        payload = decode_unlock_token(token)
        self.assertEqual(payload["sub"], "7")
        self.assertEqual(payload["type"], "unlock")

        access = create_access_token({"sub": "7", "role": "admin"})
        self.assertIsNone(decode_unlock_token(access))
        self.assertIsNone(decode_unlock_token("not-a-token"))


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """只满足 service 的 execute/flush/add；不触库。"""

    def __init__(self, row):
        self.row = row

    async def execute(self, _stmt):
        return _FakeResult(self.row)

    async def flush(self):
        pass

    def add(self, _obj):
        pass

    async def commit(self):
        pass


class LockLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self._orig_redis = auth_service.redis_client
        auth_service.redis_client = self.redis
        from app.models.user_ui_preference import UserUiPreference

        self.row = UserUiPreference(user_id=9, lock_password_hash="hash", preferences=None)
        self.db = _FakeSession(self.row)
        self.admin = SimpleNamespace(id=9, account="admin")

    async def asyncTearDown(self):
        auth_service.redis_client = self._orig_redis

    async def test_wrong_password_locks_after_limit(self):
        with mock.patch.object(
            ui_preferences_service, "verify_password_async",
            new=mock.AsyncMock(return_value=False),
        ):
            for _ in range(auth_service.LOGIN_FAIL_LIMIT):
                with self.assertRaises(AppException) as ctx:
                    await ui_preferences_service.verify_lock_password(
                        self.db, self.admin, "wrong", "127.0.0.1"
                    )
                self.assertEqual(ctx.exception.code, 400)
            with self.assertRaises(AppException) as ctx:
                await ui_preferences_service.verify_lock_password(
                    self.db, self.admin, "wrong", "127.0.0.1"
                )
        self.assertEqual(ctx.exception.code, 429)

    async def test_correct_password_clears_limit_and_returns_token(self):
        with mock.patch.object(
            ui_preferences_service, "verify_password_async",
            new=mock.AsyncMock(return_value=True),
        ):
            token = await ui_preferences_service.verify_lock_password(
                self.db, self.admin, "right", "127.0.0.1"
            )
        self.assertEqual(decode_unlock_token(token)["sub"], "9")

    async def test_not_configured_is_400(self):
        self.row.lock_password_hash = None
        with self.assertRaises(AppException) as ctx:
            await ui_preferences_service.verify_lock_password(self.db, self.admin, "x", None)
        self.assertEqual(ctx.exception.code, 400)


class PreferencesApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_returns_not_exists_when_never_saved(self):
        with mock.patch.object(
            ui_preferences_service, "get_preferences",
            new=mock.AsyncMock(return_value=(None, False)),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.get("/api/v1/admin/preferences")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"], {
            "exists": False, "preferences": None, "has_lock_password": False,
        })

    async def test_put_partial_saves_only_whitelist_and_audits(self):
        saved = ui_preferences_service.merge_preferences({}, {"menu_style": "dark"})
        with mock.patch.object(
            ui_preferences_service, "update_preferences",
            new=mock.AsyncMock(return_value=saved),
        ) as update, mock.patch.object(
            ui_preferences_service, "get_preferences",
            new=mock.AsyncMock(return_value=(saved, False)),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.put(
                    "/api/v1/admin/preferences",
                    json={"menu_style": "dark", "unknown": "ignored"},
                )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["preferences"]["menu_style"], "dark")
        self.assertNotIn("unknown", res.json()["data"]["preferences"])
        update.assert_awaited_once()
        self.assertEqual(update.await_args.args[2], {"menu_style": "dark"})

    async def test_put_invalid_value_returns_422(self):
        async with async_client(user=make_user(role="admin")) as client:
            res = await client.put(
                "/api/v1/admin/preferences", json={"menu_width": 233}
            )
        self.assertEqual(res.status_code, 422)

    async def test_set_lock_password_never_echoes_secret(self):
        with mock.patch.object(
            ui_preferences_service, "set_lock_password",
            new=mock.AsyncMock(return_value=None),
        ) as save:
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post(
                    "/api/v1/admin/lock/password", json={"password": "Secret123"}
                )
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("Secret123", res.text)
        self.assertEqual(res.json()["data"], {"has_lock_password": True})
        save.assert_awaited_once()

    async def test_set_lock_password_too_short_returns_422(self):
        async with async_client(user=make_user(role="admin")) as client:
            res = await client.post(
                "/api/v1/admin/lock/password", json={"password": "123"}
            )
        self.assertEqual(res.status_code, 422)

    async def test_verify_returns_short_lived_token(self):
        with mock.patch.object(
            ui_preferences_service, "verify_lock_password",
            new=mock.AsyncMock(return_value=create_unlock_token(1)),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post(
                    "/api/v1/admin/lock/verify", json={"password": "Secret123"}
                )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["expires_in"], 300)
        self.assertEqual(
            decode_unlock_token(res.json()["data"]["unlock_token"])["sub"], "1"
        )

    async def test_wrong_password_is_400_and_locked_is_429(self):
        for code, expected in ((400, 400), (429, 429)):
            with mock.patch.object(
                ui_preferences_service, "verify_lock_password",
                new=mock.AsyncMock(side_effect=AppException(code=code, message="x")),
            ):
                async with async_client(user=make_user(role="admin")) as client:
                    res = await client.post(
                        "/api/v1/admin/lock/verify", json={"password": "wrong"}
                    )
            self.assertEqual(res.status_code, expected)


class PreferencesRbacTests(unittest.TestCase):
    def test_routes_require_only_admin(self):
        for method, path in (
            ("GET", "/api/v1/admin/preferences"),
            ("PUT", "/api/v1/admin/preferences"),
            ("POST", "/api/v1/admin/lock/password"),
            ("POST", "/api/v1/admin/lock/verify"),
        ):
            self.assertEqual(rbac.match_route_permission(method, path), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
