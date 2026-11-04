"""登录图形验证码（captcha_service）：一次性消费、fail-closed 与登录开关语义。

钉三条不变量：

1. **一枚码只能用一次**。GETDEL 语义下同一枚码第二次必须失败 —— 否则截图
   即可无限重放，验证码退化成装饰。
2. **Redis 故障时 fail-closed**。签发与校验都拒绝（503），绝不退化成
   「验不了就放行」—— 那等于没有验证码。
3. **开关关闭时登录零成本放行**。默认关闭是刻意的：内容 API 是公开交付面，
   第三方登录页不感知开关；打开后缺码/错码都必须拦在密码校验之前。
"""

import unittest
from unittest import mock

from app.core.exceptions import AppException, AuthenticationError
from app.services import auth_service, captcha_service
from app.services.integration_settings_service import IntegrationConfig
from tests.conftest import FakeRedis


def _cfg(enabled: bool) -> IntegrationConfig:
    return IntegrationConfig({"login_captcha_enabled": "1" if enabled else "0"})


class CaptchaRoundtripTests(unittest.IsolatedAsyncioTestCase):
    """签发 → 校验的完整生命周期"""

    def setUp(self):
        self.redis = FakeRedis()
        self._orig_redis = captcha_service.redis_client
        captcha_service.redis_client = self.redis

    async def asyncTearDown(self):
        captcha_service.redis_client = self._orig_redis

    async def test_svg_shape_and_answer_not_leaked(self):
        data = await captcha_service.generate_captcha()
        self.assertIn("<svg", data["svg"])
        self.assertIn("</svg>", data["svg"])
        self.assertIn('aria-label="login captcha"', data["svg"])
        # 答案绝不能出现在响应里（明文或 base64 之外的形式都不行）
        self.assertNotIn("answer", data)
        self.assertEqual(data["expires_in"], captcha_service.CAPTCHA_TTL_SECONDS)

    async def test_correct_code_passes_once(self):
        data = await captcha_service.generate_captcha()
        code = await self.redis.get("login_captcha:" + data["captcha_id"])
        self.assertTrue(await captcha_service.verify_captcha(data["captcha_id"], str(code)))
        # 一次性消费：同一枚码第二次必须失败（防截图重放）
        self.assertFalse(await captcha_service.verify_captcha(data["captcha_id"], str(code)))

    async def test_case_insensitive_and_whitespace_tolerant(self):
        data = await captcha_service.generate_captcha()
        code = str(await self.redis.get("login_captcha:" + data["captcha_id"]))
        self.assertTrue(await captcha_service.verify_captcha(data["captcha_id"], f" {code.lower()} "))

    async def test_wrong_code_fails_but_consumes_the_captcha(self):
        data = await captcha_service.generate_captcha()
        self.assertFalse(await captcha_service.verify_captcha(data["captcha_id"], "XXXX"))
        # 错码同样烧掉这枚码：爆破者不能拿一枚码无限试
        self.assertFalse(await captcha_service.verify_captcha(data["captcha_id"], "XXXX"))

    async def test_unknown_or_blank_ids_fail(self):
        self.assertFalse(await captcha_service.verify_captcha(None, "AB2D"))
        self.assertFalse(await captcha_service.verify_captcha("deadbeef", "AB2D"))
        data = await captcha_service.generate_captcha()
        self.assertFalse(await captcha_service.verify_captcha(data["captcha_id"], None))


class CaptchaFailClosedTests(unittest.IsolatedAsyncioTestCase):
    """Redis 故障方向必须是拒绝，不是放行"""

    def setUp(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        class _BrokenRedis:
            async def set(self, *args, **kwargs):
                raise RedisConnectionError("redis down")

            async def getdel(self, *args, **kwargs):
                raise RedisConnectionError("redis down")

        self._orig_redis = captcha_service.redis_client
        captcha_service.redis_client = _BrokenRedis()

    async def asyncTearDown(self):
        captcha_service.redis_client = self._orig_redis

    async def test_generate_refuses_when_redis_down(self):
        with self.assertRaises(AppException) as ctx:
            await captcha_service.generate_captcha()
        self.assertEqual(ctx.exception.code, 503)

    async def test_verify_refuses_when_redis_down(self):
        with self.assertRaises(AppException) as ctx:
            await captcha_service.verify_captcha("any-id", "AB2D")
        self.assertEqual(ctx.exception.code, 503)


class EnforceLoginCaptchaTests(unittest.IsolatedAsyncioTestCase):
    """登录链路的收口：开关语义 + captcha_required 信号"""

    def setUp(self):
        self.redis = FakeRedis()
        self._orig_redis = captcha_service.redis_client
        captcha_service.redis_client = self.redis

    async def asyncTearDown(self):
        captcha_service.redis_client = self._orig_redis

    async def _with_cfg(self, enabled: bool):
        return mock.patch.object(
            captcha_service, "get_auth_config", mock.AsyncMock(return_value=_cfg(enabled))
        )

    async def test_disabled_passes_without_captcha(self):
        with await self._with_cfg(False):
            await captcha_service.enforce_login_captcha(None, None)

    async def test_enabled_missing_captcha_signals_captcha_required(self):
        with await self._with_cfg(True):
            with self.assertRaises(AppException) as ctx:
                await captcha_service.enforce_login_captcha(None, None)
        self.assertEqual(ctx.exception.code, 400)
        self.assertTrue(ctx.exception.data.get("captcha_required"))

    async def test_enabled_wrong_code_signals_captcha_required(self):
        data = await captcha_service.generate_captcha()
        with await self._with_cfg(True):
            with self.assertRaises(AppException) as ctx:
                await captcha_service.enforce_login_captcha(data["captcha_id"], "XXXX")
        self.assertTrue(ctx.exception.data.get("captcha_required"))

    async def test_enabled_correct_code_passes(self):
        data = await captcha_service.generate_captcha()
        code = str(await self.redis.get("login_captcha:" + data["captcha_id"]))
        with await self._with_cfg(True):
            await captcha_service.enforce_login_captcha(data["captcha_id"], code)


class LoginCaptchaEnforcementTests(unittest.IsolatedAsyncioTestCase):
    """端到端钉住顺序：验证码必须拦在密码校验之前"""

    def setUp(self):
        self._orig_redis = auth_service.redis_client
        auth_service.redis_client = FakeRedis()

    async def asyncTearDown(self):
        auth_service.redis_client = self._orig_redis

    def _req(self, **kwargs):
        from app.schemas.auth import LoginRequest

        return LoginRequest(account="user@example.com", password="whatever", **kwargs)

    async def test_enabled_blocks_login_before_password_check(self):
        """缺码 → 400 + captcha_required，且 verify_account_password 未被触碰"""
        from tests.fake_db import MemorySession

        verify = mock.AsyncMock()
        with mock.patch.object(
            captcha_service, "get_auth_config", mock.AsyncMock(return_value=_cfg(True))
        ), mock.patch.object(auth_service, "verify_account_password", verify):
            with self.assertRaises(AppException) as ctx:
                await auth_service.login(self._req(), MemorySession())
        self.assertEqual(ctx.exception.code, 400)
        self.assertTrue(ctx.exception.data.get("captcha_required"))
        verify.assert_not_awaited()

    async def test_enabled_valid_captcha_reaches_password_check(self):
        """码对了才进密码校验：密码错误时上报「账号或密码错误」（证明已过验证码阶段）"""
        from tests.fake_db import MemorySession

        with mock.patch.object(
            captcha_service, "get_auth_config", mock.AsyncMock(return_value=_cfg(True))
        ), mock.patch.object(
            captcha_service, "verify_captcha", mock.AsyncMock(return_value=True)
        ), mock.patch.object(
            auth_service, "verify_account_password", mock.AsyncMock(side_effect=AuthenticationError("账号或密码错误"))
        ):
            with self.assertRaises(AuthenticationError) as ctx:
                await auth_service.login(
                    self._req(captcha_id="abc", captcha_code="AB2D"), MemorySession()
                )
        self.assertEqual(str(ctx.exception), "账号或密码错误")

    async def test_disabled_login_needs_no_captcha(self):
        from tests.fake_db import MemorySession

        with mock.patch.object(
            captcha_service, "get_auth_config", mock.AsyncMock(return_value=_cfg(False))
        ), mock.patch.object(
            auth_service, "verify_account_password", mock.AsyncMock(side_effect=AuthenticationError("账号或密码错误"))
        ):
            with self.assertRaises(AuthenticationError) as ctx:
                await auth_service.login(self._req(), MemorySession())
        self.assertEqual(str(ctx.exception), "账号或密码错误")


if __name__ == "__main__":
    unittest.main()
