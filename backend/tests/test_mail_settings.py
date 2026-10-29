"""邮件通知模块（2026-10-27 从「系统设置 → 邮件通知」抽出）守卫。

抽出的理由不是配置项多，而是它**默认就是坏的**：SMTP 没配齐时生产环境发不出验证码，
用户只看到「发送失败」。所以除了「归属 / 写范围 / 权限」这套通用守卫，本文件重点钉：

1. **状态可信**：`ready` 必须与「真的能发」同源（`utils/mailer.missing_fields`），
   不能自己再数一遍必需项 —— 两处清单一旦分叉，就会出现「后台显示已就绪、发信失败」；
2. **不泄露凭据**：状态里只出现 host:port 与发件人，授权码连片段都不能有；
3. **TLS 判定**：465 = 隐式 TLS、587 = STARTTLS —— 这正是设置项文案承诺的行为，
   而验证码发信此前只传 `start_tls`，填 465 的部署会一直发不出去。
"""

import unittest
from unittest import mock

from sqlalchemy import select

from app.config import settings
from app.core.rbac import match_route_permission
from app.models.system_setting import SystemSetting
from app.services import mail_settings_service, setting_service
from app.services.setting_service import (
    SURFACE_LEGAL,
    SURFACE_LOGIN,
    SURFACE_MAIL,
    SURFACE_MODEL,
    SURFACE_PAYMENT,
    SURFACE_SETTINGS,
)
from app.utils import mailer
from tests.fake_db import MemorySession

_MAIL_KEYS = {
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_use_tls",
    "smtp_from_email",
    "smtp_sender_name",
}


def _clear_smtp():
    """把 SMTP 相关配置清空（构造「刚部署、还没配」的状态）"""
    return mock.patch.multiple(
        settings,
        SMTP_HOST="",
        SMTP_PORT=0,
        SMTP_USERNAME="",
        SMTP_PASSWORD="",
        SMTP_FROM_EMAIL="",
        SMTP_SENDER_NAME="",
    )


class OwnershipTests(unittest.TestCase):
    """① 归属：只属于邮件通知页，系统设置里不再出现"""

    def test_module_owns_exactly_the_mail_keys(self):
        self.assertEqual(set(mail_settings_service.MAIL_SETTING_KEYS), _MAIL_KEYS)

    def test_mail_keys_are_mail_surface_only(self):
        for surface in (
            SURFACE_SETTINGS,
            SURFACE_LOGIN,
            SURFACE_LEGAL,
            SURFACE_PAYMENT,
            SURFACE_MODEL,
        ):
            with self.subTest(surface=surface):
                self.assertFalse(_MAIL_KEYS & setting_service.keys_for_surface(surface))

    def test_system_settings_payload_has_no_mail_group(self):
        groups = {g["key"] for g in setting_service.groups_payload(surface=SURFACE_SETTINGS)}
        self.assertNotIn("mail", groups)
        defs = setting_service.definitions_payload(surface=SURFACE_SETTINGS)
        self.assertFalse([d["key"] for d in defs if d["key"] in _MAIL_KEYS])


class WriteScopeTests(unittest.IsolatedAsyncioTestCase):
    """② 写通道只能写邮件配置"""

    async def asyncSetUp(self):
        self.db = MemorySession()

    async def _rows(self):
        return {
            r.setting_key: r.value
            for r in (await self.db.execute(select(SystemSetting))).scalars().all()
        }

    async def test_accepts_mail_keys(self):
        changed = await mail_settings_service.update(self.db, {"smtp_host": "smtp.example.com"})
        await self.db.commit()
        self.assertEqual(changed, 1)
        self.assertEqual((await self._rows())["smtp_host"], "smtp.example.com")

    async def test_rejects_foreign_keys(self):
        for key in ("cos_secret_key", "payment_enabled", "ai_global_daily_limit", "site_name"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    await mail_settings_service.update(
                        self.db, {"smtp_host": "smtp.example.com", key: "injected"}
                    )
        self.assertEqual(await self._rows(), {}, "越界写入没有被整批放弃")

    async def test_password_stored_encrypted_and_masked(self):
        from app.core import crypto

        await mail_settings_service.update(self.db, {"smtp_password": "PLAIN-AUTH-CODE"})
        await self.db.commit()
        self.assertTrue(crypto.is_encrypted((await self._rows())["smtp_password"]))
        values = await mail_settings_service.stored_values(self.db)
        self.assertTrue(values["smtp_password"].startswith(crypto.MASK_PREFIX))


class ChannelStatusTests(unittest.TestCase):
    """③ 通道状态：缺什么、有没有加密、绝不回显凭据"""

    def test_missing_fields_are_reported_without_values(self):
        with _clear_smtp():
            status = mail_settings_service.channel_status()
        self.assertFalse(status["ready"])
        self.assertEqual(len(status["missing"]), 5)
        self.assertTrue(status["issues"])
        self.assertEqual(status["endpoint"], "")

    def test_ready_when_configured(self):
        with mock.patch.multiple(
            settings,
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=587,
            SMTP_USERNAME="bot@example.com",
            SMTP_PASSWORD="secret-code",
            SMTP_FROM_EMAIL="noreply@example.com",
            SMTP_USE_TLS=True,
        ):
            status = mail_settings_service.channel_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["endpoint"], "smtp.example.com:587")
        self.assertEqual(status["tls"], "starttls")
        self.assertEqual(status["missing"], [])

    def test_status_never_leaks_credentials(self):
        with mock.patch.multiple(
            settings,
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=587,
            SMTP_USERNAME="bot@example.com",
            SMTP_PASSWORD="super-secret-auth-code",
            SMTP_FROM_EMAIL="noreply@example.com",
            SMTP_USE_TLS=True,
        ):
            payload = repr(mail_settings_service.channel_status())
        self.assertNotIn("super-secret-auth-code", payload)
        self.assertNotIn("bot@example.com", payload, "SMTP 账号不该出现在状态里")

    def test_unencrypted_channel_is_flagged(self):
        with mock.patch.multiple(
            settings,
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=25,
            SMTP_USERNAME="bot@example.com",
            SMTP_PASSWORD="code",
            SMTP_FROM_EMAIL="noreply@example.com",
            SMTP_USE_TLS=False,
        ):
            status = mail_settings_service.channel_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["tls"], "none")
        self.assertTrue(status["issues"], "明文发信必须给出提示")


class TlsModeTests(unittest.TestCase):
    """④ 465 = 隐式 TLS，587/25 = STARTTLS（设置项文案承诺的行为）"""

    def test_port_465_uses_implicit_tls(self):
        with mock.patch.multiple(
            settings, SMTP_HOST="smtp.example.com", SMTP_PORT=465, SMTP_USE_TLS=True
        ):
            kwargs = mailer.connect_kwargs()
            mode = mailer.tls_mode()
        self.assertTrue(kwargs["use_tls"])
        self.assertFalse(kwargs["start_tls"], "隐式 TLS 与 STARTTLS 不能同时开")
        self.assertEqual(mode, "ssl")

    def test_port_587_uses_starttls(self):
        with mock.patch.multiple(
            settings, SMTP_HOST="smtp.example.com", SMTP_PORT=587, SMTP_USE_TLS=True
        ):
            kwargs = mailer.connect_kwargs()
            mode = mailer.tls_mode()
        self.assertFalse(kwargs["use_tls"])
        self.assertTrue(kwargs["start_tls"])
        self.assertEqual(mode, "starttls")

    def test_verification_send_uses_the_same_connection_kwargs(self):
        """验证码邮件与测试邮件必须同一套连接参数，否则「测试通了真实发不出」"""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "app" / "utils" / "verification.py"
        ).read_text(encoding="utf-8")
        self.assertIn("connect_kwargs()", source)


class TestSendTests(unittest.IsolatedAsyncioTestCase):
    """⑤ 测试邮件：地址校验、冷却、失败原因可读"""

    async def asyncSetUp(self):
        mail_settings_service.reset_rate_limit()

    async def asyncTearDown(self):
        mail_settings_service.reset_rate_limit()

    def test_rejects_bad_recipient(self):
        for bad in ("", "abc", "a@b", "a b@c.com", "x" * 250 + "@a.com"):
            with self.subTest(to=bad):
                with self.assertRaises(ValueError):
                    mail_settings_service.validate_recipient(bad)

    def test_accepts_normal_address(self):
        self.assertEqual(
            mail_settings_service.validate_recipient("  Ops.Team+a@Example.com "),
            "Ops.Team+a@Example.com",
        )

    def test_rate_limit_blocks_second_click(self):
        mail_settings_service.check_rate_limit()
        with self.assertRaises(RuntimeError):
            mail_settings_service.check_rate_limit()

    async def test_send_returns_reason_on_failure(self):
        with mock.patch.object(
            mail_settings_service, "send_plain_email", return_value=(False, "SMTPConnectError: 连不上")
        ):
            ok, detail = await mail_settings_service.send_test_email("ops@example.com")
        self.assertFalse(ok)
        self.assertIn("连不上", detail)

    async def test_test_mail_subject_is_unambiguous(self):
        captured = {}

        async def fake_send(to_email, subject, body):
            captured.update(to=to_email, subject=subject, body=body)
            return True, ""

        with mock.patch.object(mail_settings_service, "send_plain_email", fake_send):
            ok, _ = await mail_settings_service.send_test_email("ops@example.com")
        self.assertTrue(ok)
        self.assertIn("测试", captured["subject"])
        self.assertIn("测试邮件", captured["body"])


class RouteTests(unittest.TestCase):
    """⑥ 路由与权限码"""

    def test_permission_codes(self):
        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/mail-settings"), ["mail_setting:view"]
        )
        self.assertEqual(
            match_route_permission("PUT", "/api/v1/admin/mail-settings"), ["mail_setting:update"]
        )
        self.assertEqual(
            match_route_permission("POST", "/api/v1/admin/mail-settings/test"),
            ["mail_setting:update"],
        )

    def test_routes_registered(self):
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/api/v1/admin/mail-settings", paths)
        self.assertIn("/api/v1/admin/mail-settings/test", paths)

    def test_menu_entry_exists(self):
        from app.services.admin_menu import iter_menu_items

        hit = [i for i in iter_menu_items() if i.key == "/mail-settings"]
        self.assertEqual(len(hit), 1, "菜单里应当有且只有一条 /mail-settings")
        self.assertEqual(hit[0].perm, "mail_setting:view")
