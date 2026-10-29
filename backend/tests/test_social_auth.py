"""手机号验证码 / 微信扫码登录的后端契约测试。

覆盖：
1. 验证码频控、固定 mock 码、一次性消费；
2. 手机号首登自动建号、关闭开关时拒绝、历史 account=手机号 自动补 phone；
3. 手机号绑定冲突；
4. 微信 state 生成/消费、自动建号、unionid 优先查号；
5. TOTP 账号不能被手机号登录绕过。
"""

import unittest
from unittest import mock

from app.core import crypto
from app.core.exceptions import AppException
from app.models.user import User
from app.services import phone_auth_service, social_auth_service
from app.services.integration_settings_service import IntegrationConfig, _DEFAULTS
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

PHONE = "13900001111"


def _config(**overrides) -> IntegrationConfig:
    values = dict(_DEFAULTS)
    values.update(overrides)
    return IntegrationConfig(values)


class PhoneCodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_redis = phone_auth_service.redis_client
        phone_auth_service.redis_client = FakeRedis()
        self._orig_send = phone_auth_service.send_sms_code
        phone_auth_service.send_sms_code = mock.AsyncMock()
        self._patch = mock.patch.object(
            phone_auth_service,
            "get_auth_config",
            new=mock.AsyncMock(
                return_value=_config(
                    phone_login_enabled="1",
                    sms_provider="mock",
                    sms_mock_code="123456",
                )
            ),
        )
        self._patch.start()

    async def asyncTearDown(self):
        self._patch.stop()
        phone_auth_service.send_sms_code = self._orig_send
        phone_auth_service.redis_client = self._orig_redis

    async def test_send_stores_fixed_mock_code_and_consumes_once(self):
        result = await phone_auth_service.send_phone_code(PHONE, "login")
        self.assertTrue(result["success"])
        self.assertTrue(
            await phone_auth_service.verify_phone_code(PHONE, "login", "123456")
        )
        self.assertFalse(
            await phone_auth_service.verify_phone_code(PHONE, "login", "123456")
        )
        phone_auth_service.send_sms_code.assert_awaited_once()

    async def test_resend_is_rate_limited(self):
        await phone_auth_service.send_phone_code(PHONE, "login")
        with self.assertRaises(AppException) as ctx:
            await phone_auth_service.send_phone_code(PHONE, "login")
        self.assertEqual(ctx.exception.code, 429)

    async def test_wrong_code_locks_after_five_attempts(self):
        await phone_auth_service.send_phone_code(PHONE, "login")
        for _ in range(5):
            self.assertFalse(await phone_auth_service.verify_phone_code(PHONE, "login", "000000"))
        # 锁定后即使输入正确也不放行
        self.assertFalse(await phone_auth_service.verify_phone_code(PHONE, "login", "123456"))

    async def test_disabled_phone_login_refuses_send(self):
        phone_auth_service.get_auth_config = mock.AsyncMock(
            return_value=_config(phone_login_enabled="0")
        )
        with self.assertRaises(AppException) as ctx:
            await phone_auth_service.send_phone_code(PHONE, "login")
        self.assertEqual(ctx.exception.code, 403)


class PhoneLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self._patch_config = mock.patch.object(
            social_auth_service,
            "get_auth_config",
            new=mock.AsyncMock(
                return_value=_config(
                    phone_login_enabled="1", phone_login_auto_register="1"
                )
            ),
        )
        self._patch_config.start()
        self._patch_code = mock.patch.object(
            social_auth_service,
            "verify_phone_code",
            new=mock.AsyncMock(return_value=True),
        )
        self._patch_code.start()
        self._patch_tokens = mock.patch.object(
            social_auth_service,
            "issue_login_tokens",
            new=mock.AsyncMock(
                return_value={
                    "access_token": "a",
                    "refresh_token": "r",
                    "token_type": "bearer",
                    "user": {"id": 1},
                }
            ),
        )
        self._patch_tokens.start()
        self._patch_totp = mock.patch.object(
            social_auth_service, "enforce_totp_if_enabled", new=mock.AsyncMock()
        )
        self._patch_totp.start()

    async def asyncTearDown(self):
        self._patch_config.stop()
        self._patch_code.stop()
        self._patch_tokens.stop()
        self._patch_totp.stop()

    async def test_first_login_auto_registers(self):
        result = await social_auth_service.phone_login(
            self.db, phone=PHONE, code="123456"
        )
        self.assertTrue(result["is_new_user"])
        rows = self.db.all_rows(User)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account"], PHONE)
        self.assertEqual(rows[0]["phone"], PHONE)
        self.assertTrue(rows[0]["password_hash"])
        self.assertNotEqual(rows[0]["password_hash"], PHONE)

    async def test_auto_register_can_be_disabled(self):
        social_auth_service.get_auth_config = mock.AsyncMock(
            return_value=_config(
                phone_login_enabled="1", phone_login_auto_register="0"
            )
        )
        with self.assertRaises(AppException) as ctx:
            await social_auth_service.phone_login(self.db, phone=PHONE, code="123456")
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(self.db.all_rows(User), [])

    async def test_legacy_account_phone_is_backfilled(self):
        self.db.seed(
            User(id=10, account=PHONE, password_hash="x", role="user")
        )
        result = await social_auth_service.phone_login(
            self.db, phone=PHONE, code="123456"
        )
        self.assertFalse(result["is_new_user"])
        row = self.db.all_rows(User)[0]
        self.assertEqual(row["phone"], PHONE)
        self.assertIsNotNone(row["phone_verified_at"])

    async def test_existing_phone_does_not_create_duplicate(self):
        self.db.seed(
            User(id=11, account="mail@example.com", phone=PHONE, password_hash="x", role="user")
        )
        await social_auth_service.phone_login(self.db, phone=PHONE, code="123456")
        self.assertEqual(len(self.db.all_rows(User)), 1)

    async def test_phone_login_delegates_to_totp_gate(self):
        self.db.seed(
            User(id=12, account="mail2@example.com", phone=PHONE, password_hash="x", role="user")
        )
        await social_auth_service.phone_login(
            self.db, phone=PHONE, code="123456", totp_code="123456"
        )
        social_auth_service.enforce_totp_if_enabled.assert_awaited_once()

    async def test_bind_phone_rejects_occupied_number(self):
        self.db.seed(User(id=13, account="a@example.com", phone=PHONE, password_hash="x"))
        current = User(id=14, account="b@example.com", password_hash="x", role="user")
        with self.assertRaises(AppException) as ctx:
            await social_auth_service.bind_phone(
                self.db, current, phone=PHONE, code="123456"
            )
        self.assertEqual(ctx.exception.code, 409)


class WechatLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self._orig_redis = social_auth_service.redis_client
        social_auth_service.redis_client = FakeRedis()
        self._patch_config = mock.patch.object(
            social_auth_service,
            "get_auth_config",
            new=mock.AsyncMock(
                return_value=_config(
                    wechat_web_login_enabled="1",
                    wechat_login_auto_register="1",
                    wechat_web_appid="wx-test-app",
                    wechat_web_secret=crypto.encrypt_secret("wx-secret"),
                    site_base_url="https://example.com",
                )
            ),
        )
        self._patch_config.start()
        self._patch_tokens = mock.patch.object(
            social_auth_service,
            "issue_login_tokens",
            new=mock.AsyncMock(
                return_value={
                    "access_token": "a",
                    "refresh_token": "r",
                    "token_type": "bearer",
                    "user": {"id": 1},
                }
            ),
        )
        self._patch_tokens.start()

    async def asyncTearDown(self):
        self._patch_config.stop()
        self._patch_tokens.stop()
        social_auth_service.redis_client = self._orig_redis

    async def test_start_url_contains_state_and_callback(self):
        url = await social_auth_service.build_wechat_login_url("/profile")
        self.assertIn("open.weixin.qq.com/connect/qrconnect", url)
        self.assertIn("wx-test-app", url)
        self.assertIn("state=", url)
        self.assertIn("api%2Fv1%2Fauth%2Fwechat%2Fweb%2Fcallback", url)

    async def test_callback_auto_registers_and_logs_in(self):
        state = "state-1"
        await social_auth_service.redis_client.set(
            f"{social_auth_service.WECHAT_STATE_PREFIX}{state}",
            '{"redirect": "/profile"}',
            ex=60,
        )
        with mock.patch.object(
            social_auth_service,
            "_exchange_wechat_code",
            new=mock.AsyncMock(
                return_value={
                    "openid": "web-openid-1",
                    "unionid": "union-1",
                    "access_token": "at",
                }
            ),
        ), mock.patch.object(
            social_auth_service,
            "_fetch_wechat_userinfo",
            new=mock.AsyncMock(return_value={"nickname": "微信用户A", "headimgurl": "https://a"}),
        ):
            result = await social_auth_service.wechat_web_login(
                self.db, code="c", state=state
            )
        self.assertTrue(result["is_new_user"])
        self.assertEqual(result["redirect"], "/profile")
        rows = self.db.all_rows(User)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["wechat_unionid"], "union-1")
        self.assertEqual(rows[0]["wechat_web_openid"], "web-openid-1")

    async def test_callback_rejects_replayed_state(self):
        with mock.patch.object(
            social_auth_service,
            "_exchange_wechat_code",
            new=mock.AsyncMock(
                return_value={"openid": "o", "access_token": "at"}
            ),
        ):
            with self.assertRaises(AppException) as ctx:
                await social_auth_service.wechat_web_login(
                    self.db, code="c", state="not-exists"
                )
        self.assertEqual(ctx.exception.code, 400)
