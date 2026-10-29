"""登录管理模块守卫（2026-10-27 从「系统设置 → 登录与短信」抽出）。

「把一组设置抽成独立模块」这类重构最容易留下三类尾巴，全都**不报错**：

1. **双写**：新页面加了、老页面没减 —— 同一批开关出现在两个 tab，各自保存一次、
   互相覆盖，排查时只会看到「我明明开了，怎么又关了」；
2. **权限收窄没落到实处**：读写改用 `login_setting:*`，但写通道仍复用未限定范围的
   `update_settings` —— 拿着「能开关登录方式」的人顺手就能改 COS 密钥；
3. **开关开着但不可用**：页面只表达「开/关」，表达不了「开着但凭据没配齐」，
   那恰恰是登录配置最常见的事故（用户能点、点了必失败）。

本文件分别钉住这三条，外加路由规则与「配置键真的被登录链路消费」的闭环。
"""

import unittest

from sqlalchemy import select

from app.core import crypto
from app.core.rbac import match_route_permission
from app.models.system_setting import SystemSetting
from app.services import login_settings_service, setting_service
from app.services.integration_settings_service import AUTH_KEYS, IntegrationConfig
from app.services.setting_service import (
    SURFACE_LOGIN,
    SURFACE_SETTINGS,
)
from tests.fake_db import MemorySession


def _config(**overrides) -> IntegrationConfig:
    """构造一份集成配置快照；密钥类字段写密文（与库里一致，secret() 会解密）"""
    values: dict[str, str] = {}
    for key, raw in overrides.items():
        if key in ("wechat_web_secret", "wechat_mini_secret", "sms_tencent_secret_key",
                   "sms_aliyun_access_key_secret", "sms_tencent_secret_id"):
            values[key] = crypto.encrypt_secret(raw)
        else:
            values[key] = raw
    return IntegrationConfig(values)


class LoginGroupTests(unittest.TestCase):
    """① 登录管理页只下发自己的分组（跨界面的键集合守卫见 test_setting_surfaces.py）"""

    def test_login_payload_only_has_login_groups(self):
        """四个区块的顺序即页面区块顺序：手机号 → 微信扫码 → 小程序凭据 → 短信通道

        `miniapp` 自 2026-10-27 从「系统设置 → 小程序」归位到此：这两个键的唯一消费方
        是 `miniapp_service.code_to_session`（登录 / 绑定），留在系统设置会让
        「页面说缺凭据、却要跳菜单才能填」。
        """
        groups = [g["key"] for g in login_settings_service.groups()]
        self.assertEqual(groups, ["auth_phone", "auth_wechat", "miniapp", "auth_sms"])

    def test_login_keys_are_consumed_by_auth_config(self):
        """登录管理页写下的键，必须真的在登录链路的读取清单里

        反向漏项（键在页面上、运行时读不到）表现为「后台配了、登录还是失败」，
        而两处都各自「看起来对」。
        """
        unknown = setting_service.keys_for_surface(SURFACE_LOGIN) - set(AUTH_KEYS)
        self.assertEqual(unknown, set(), f"这些登录设置项没有进 AUTH_KEYS，运行时读不到: {sorted(unknown)}")


class WriteScopeTests(unittest.IsolatedAsyncioTestCase):
    """② 登录写入通道只能写登录键（权限收窄必须落到写通道上）"""

    async def asyncSetUp(self):
        self.db = MemorySession()

    async def _rows(self):
        return {
            r.setting_key: r.value
            for r in (await self.db.execute(select(SystemSetting))).scalars().all()
        }

    async def test_login_update_accepts_login_keys(self):
        changed = await login_settings_service.update(self.db, {"phone_login_enabled": "1"})
        await self.db.commit()
        self.assertEqual(changed, 1)
        self.assertEqual((await self._rows())["phone_login_enabled"], "1")

    async def test_login_update_rejects_foreign_key(self):
        """越界键必须整批拒绝 —— 不能出现「登录配置写了、密钥也顺手改了一半」"""
        with self.assertRaises(ValueError):
            await login_settings_service.update(
                self.db, {"phone_login_enabled": "1", "cos_secret_key": "sk-injected"}
            )
        self.assertNotIn("cos_secret_key", await self._rows())
        self.assertNotIn("phone_login_enabled", await self._rows())

    async def test_settings_update_rejects_login_key(self):
        """反向也要拦：系统设置不该还能改登录配置"""
        with self.assertRaises(ValueError):
            await setting_service.update_settings(
                self.db, {"wechat_web_secret": "x"}, surface=SURFACE_SETTINGS
            )

    async def test_login_values_are_surface_limited(self):
        """GET 也不该把 COS/支付密钥的掩码带到登录管理页（最小暴露面）"""
        await setting_service.update_settings(self.db, {"cos_secret_key": "sk-cos"})
        await setting_service.update_settings(self.db, {"wechat_web_secret": "wx-secret"})
        await self.db.commit()
        values = await login_settings_service.stored_values(self.db)
        self.assertIn("wechat_web_secret", values)
        self.assertNotIn("cos_secret_key", values)


class ChannelOverviewTests(unittest.TestCase):
    """③ 渠道可用性：必须能表达「开着但不可用」"""

    def _channel(self, channels, key):
        return next(c for c in channels if c["key"] == key)

    def test_mock_provider_marks_phone_unready(self):
        channels = login_settings_service.channel_overview(
            _config(phone_login_enabled="1", sms_provider="mock")
        )
        phone = self._channel(channels, "phone")
        self.assertTrue(phone["enabled"])
        self.assertFalse(phone["ready"], "Mock 服务商下手机号登录不该被判为可用")
        self.assertTrue(phone["issues"], "开着但发不出短信时必须有明确提示")

    def test_tencent_ready_when_all_credentials_present(self):
        channels = login_settings_service.channel_overview(
            _config(
                phone_login_enabled="1",
                sms_provider="tencent",
                sms_tencent_secret_id="AKID-xxx",
                sms_tencent_secret_key="sk-tencent",
                sms_tencent_sdk_app_id="1400000000",
                sms_tencent_sign_name="签名",
                sms_tencent_template_id="123456",
            )
        )
        phone = self._channel(channels, "phone")
        self.assertTrue(phone["ready"])
        self.assertEqual(phone["issues"], [])

    def test_aliyun_missing_credentials_marks_unready(self):
        channels = login_settings_service.channel_overview(
            _config(phone_login_enabled="1", sms_provider="aliyun", sms_aliyun_sign_name="签名")
        )
        phone = self._channel(channels, "phone")
        self.assertFalse(phone["ready"])
        self.assertTrue(phone["issues"])

    def test_wechat_web_requires_both_appid_and_secret(self):
        only_appid = login_settings_service.channel_overview(
            _config(wechat_web_login_enabled="1", wechat_web_appid="wx123")
        )
        self.assertFalse(self._channel(only_appid, "wechat_web")["ready"])

        both = login_settings_service.channel_overview(
            _config(wechat_web_login_enabled="1", wechat_web_appid="wx123", wechat_web_secret="wx-secret")
        )
        self.assertTrue(self._channel(both, "wechat_web")["ready"])

    def test_disabled_channel_reports_no_issues(self):
        """关掉的渠道不该刷提示（否则页面上全是无需处理的警告）"""
        channels = login_settings_service.channel_overview(
            _config(phone_login_enabled="0", sms_provider="mock", wechat_web_login_enabled="0")
        )
        for key in ("phone", "wechat_web", "wechat_mini"):
            with self.subTest(channel=key):
                channel = self._channel(channels, key)
                self.assertFalse(channel["enabled"])
                self.assertEqual(channel["issues"], [])

    def test_email_channel_always_available(self):
        channels = login_settings_service.channel_overview(_config())
        email = self._channel(channels, "email")
        self.assertTrue(email["enabled"] and email["ready"])

    def test_overview_never_leaks_credentials(self):
        """摘要里绝不能出现凭据的任何片段（该接口是 login_setting:view，比 setting:view 更易被授出）"""
        secret_web = "wx-live-secret-do-not-leak"
        secret_sms = "tencent-key-do-not-leak"
        channels = login_settings_service.channel_overview(
            _config(
                wechat_web_login_enabled="1",
                wechat_web_appid="wx-appid-public",
                wechat_web_secret=secret_web,
                phone_login_enabled="1",
                sms_provider="tencent",
                sms_tencent_secret_key=secret_sms,
            )
        )
        blob = repr(channels)
        self.assertNotIn(secret_web, blob)
        self.assertNotIn(secret_sms, blob)


class RouteGuardTests(unittest.TestCase):
    """读写各用独立权限码；接口必须真实注册"""

    def test_route_permissions(self):
        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/login-settings"), ["login_setting:view"]
        )
        self.assertEqual(
            match_route_permission("PUT", "/api/v1/admin/login-settings"), ["login_setting:update"]
        )

    def test_endpoint_registered(self):
        from app.main import app

        self.assertIn("/api/v1/admin/login-settings", app.openapi()["paths"])

    def test_admin_settings_no_longer_serves_login_keys(self):
        """系统设置端点的定义载荷里不能出现登录键（双写的入口必须彻底关掉）"""
        from app.api.admin_settings import admin_get_settings  # noqa: F401  仅确认模块可导入

        defs = setting_service.definitions_payload(surface=SURFACE_SETTINGS)
        keys = {d["key"] for d in defs}
        self.assertFalse(keys & setting_service.keys_for_surface(SURFACE_LOGIN))


if __name__ == "__main__":
    unittest.main()
