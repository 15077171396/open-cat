"""管理员强制 2FA：策略开关的安全边界。

三条最容易把管理员锁在门外的路径：
1. 开关打开时仍有未绑定 TOTP 的管理员 → 下一次登录直接失效；
2. 管理员在强制期间从个人中心关闭 TOTP → 绕过策略；
3. 通过建号 / 提权造出一个没有 TOTP 的新管理员 → 绕过策略。
"""

import unittest
from unittest import mock

from app.core.exceptions import AppException
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.schemas.auth import LoginRequest
from app.services import auth_service, setting_service, totp_service
from tests.fake_db import MemorySession


def _admin(**kw) -> User:
    defaults = dict(
        id=1,
        account="admin@test",
        password_hash="x",
        role="admin",
        totp_enabled=False,
        totp_secret=None,
    )
    defaults.update(kw)
    return User(**defaults)


class PolicyEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def test_unbound_admin_is_blocked_only_when_policy_on(self):
        with mock.patch.object(
            setting_service, "get_bool_setting", mock.AsyncMock(return_value=True)
        ):
            with self.assertRaises(AppException) as ctx:
                await totp_service.enforce_admin_2fa_policy(MemorySession(), _admin())
            self.assertEqual(ctx.exception.code, 403)

        with mock.patch.object(
            setting_service, "get_bool_setting", mock.AsyncMock(return_value=False)
        ):
            await totp_service.enforce_admin_2fa_policy(MemorySession(), _admin())

        # 普通用户不受管理员策略影响
        with mock.patch.object(
            setting_service, "get_bool_setting", mock.AsyncMock(return_value=True)
        ):
            await totp_service.enforce_admin_2fa_policy(
                MemorySession(), _admin(role="user")
            )

    async def test_login_rejects_unbound_admin(self):
        user = _admin()
        with (
            mock.patch.object(
                auth_service, "verify_account_password", mock.AsyncMock(return_value=user)
            ),
            mock.patch.object(
                setting_service, "get_bool_setting", mock.AsyncMock(return_value=True)
            ),
        ):
            with self.assertRaises(AppException) as ctx:
                await auth_service.login(
                    LoginRequest(account="admin@test", password="pw"), MemorySession()
                )
        self.assertEqual(ctx.exception.code, 403)
        self.assertIn("二次验证", ctx.exception.message)

    async def test_admin_cannot_disable_totp_while_policy_on(self):
        user = _admin(totp_enabled=True, totp_secret="encrypted")
        with mock.patch.object(
            setting_service, "get_bool_setting", mock.AsyncMock(return_value=True)
        ):
            with self.assertRaises(AppException) as ctx:
                await totp_service.disable_totp(MemorySession(), user, "pw")
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("不能关闭", ctx.exception.message)

    async def test_creating_admin_is_blocked_while_policy_on(self):
        from app.services import admin_user_service

        with mock.patch.object(
            setting_service, "get_bool_setting", mock.AsyncMock(return_value=True)
        ):
            with self.assertRaises(AppException) as ctx:
                await admin_user_service.create_user(
                    MemorySession(),
                    _admin(id=99, totp_enabled=True),
                    {"account": "new@test", "password": "pw123456", "role": "admin"},
                )
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("普通账号", ctx.exception.message)


class PolicyUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_cannot_enable_policy_with_unbound_admin(self):
        db = MemorySession()
        db.seed(_admin())
        with mock.patch.object(
            setting_service, "invalidate_public_settings_cache", mock.AsyncMock()
        ):
            with self.assertRaises(ValueError) as ctx:
                await setting_service.update_settings(db, {"admin_require_2fa": "1"})
        self.assertIn("未开启二次验证", str(ctx.exception))
        self.assertEqual(db.all_rows(SystemSetting), [], "校验失败不得写入设置行")

    async def test_can_enable_policy_when_all_admins_bound(self):
        db = MemorySession()
        db.seed(_admin(totp_enabled=True, totp_secret="encrypted"))
        with mock.patch.object(
            setting_service, "invalidate_public_settings_cache", mock.AsyncMock()
        ):
            changed = await setting_service.update_settings(db, {"admin_require_2fa": "1"})
        self.assertEqual(changed, 1)
        await db.commit()
        self.assertEqual(db.all_rows(SystemSetting)[0]["setting_key"], "admin_require_2fa")
        self.assertEqual(db.all_rows(SystemSetting)[0]["value"], "1")


if __name__ == "__main__":
    unittest.main()
