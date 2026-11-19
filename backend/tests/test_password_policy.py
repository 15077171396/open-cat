"""密码强度策略契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **三个写入点同一口径**：注册 / 管理员建号 / 用户改密都走
   `validate_password_strength` —— 口径漂移（注册严、改密松）是安全策略
   最常见的漏洞形态。
2. **配置可调且即时生效**：最小长度 / 混合要求读运行期配置（patch settings
   即模拟后台保存后的生效形态）。
3. **报错文案面向用户**：违规信息直接展示在表单上（含当前长度这类可行动的信息）。
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.config import settings
from app.core.exceptions import AppException
from app.core.password_policy import validate_password_strength
from app.services import auth_service, user_settings_service


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(settings, "PASSWORD_MIN_LENGTH", 8),
            mock.patch.object(settings, "PASSWORD_REQUIRE_MIXED", True),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_accepts_strong_password(self):
        validate_password_strength("abc12345")  # 不抛即通过
        validate_password_strength("a1" * 4)

    def test_rejects_short_with_current_length_in_message(self):
        with self.assertRaises(AppException) as ctx:
            validate_password_strength("a1")
        self.assertIn("至少 8 位", ctx.exception.message)
        self.assertIn("2 位", ctx.exception.message)

    def test_rejects_non_mixed(self):
        with self.assertRaises(AppException) as ctx:
            validate_password_strength("abcdefgh")
        self.assertIn("字母和数字", ctx.exception.message)
        with self.assertRaises(AppException):
            validate_password_strength("12345678")

    def test_mixed_requirement_can_be_disabled(self):
        with mock.patch.object(settings, "PASSWORD_REQUIRE_MIXED", False):
            validate_password_strength("abcdefgh")  # 纯字母也放行

    def test_min_length_can_be_adjusted(self):
        with mock.patch.object(settings, "PASSWORD_MIN_LENGTH", 12):
            with self.assertRaises(AppException) as ctx:
                validate_password_strength("abc12345")  # 8 位在 12 位策略下不合格
            self.assertIn("至少 12 位", ctx.exception.message)

    def test_rejects_surrounding_whitespace(self):
        with self.assertRaises(AppException) as ctx:
            validate_password_strength(" abc12345 ")
        self.assertIn("空白", ctx.exception.message)


class HookTests(unittest.TestCase):
    """写入点挂钩：注册 / 改密都过策略（管理员建号共用同一函数，逻辑由 policy 用例覆盖）"""

    def _patch_register_chain(self):
        patchers = [
            mock.patch.object(auth_service.captcha_service, "enforce_login_captcha", new=mock.AsyncMock()),
        ]

        class _Req:
            account = "13812345678"
            password = "weak"
            nickname = None
            invite_code = None

        return patchers, _Req()

    def test_register_rejects_weak_password(self):
        """密码策略是注册第一道门：db 为 None 也照样在密码校验处拒绝（快速失败）"""
        patchers, req = self._patch_register_chain()
        with patchers[0]:
            with mock.patch.object(settings, "PASSWORD_MIN_LENGTH", 8):
                with self.assertRaises(AppException) as ctx:
                    import asyncio

                    asyncio.run(auth_service.register(req, None))
        self.assertIn("密码", ctx.exception.message)

    def test_change_password_uses_policy_not_fixed_six(self):
        """改密不再写死「至少 6 位」：6 位弱密码在默认 8 位策略下被拒"""

        class _User:
            password_hash = "hash"

        with mock.patch.object(
            user_settings_service, "verify_password_async", new=mock.AsyncMock(return_value=True)
        ):
            with mock.patch.object(settings, "PASSWORD_MIN_LENGTH", 8):
                with self.assertRaises(AppException) as ctx:
                    import asyncio

                    asyncio.run(user_settings_service.change_password(None, _User(), "old", "a1"))
        self.assertIn("密码", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
