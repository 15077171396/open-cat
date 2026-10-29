"""支付与渠道模块守卫（2026-10-27 从「系统设置 → 支付与渠道」抽出）。

与前两个抽出的模块（登录管理 / 法律文档）同一套守卫，另加支付特有的三条 ——
这三条都属于「不报错但会让钱不对」的静默故障：

1. **Mock 支付**：开关开着时要区分「真的可用（非生产环境）」与「被环境拒绝」，
   前者是隐患（用户不付钱直接到账），必须能被页面读出来；
2. **渠道开着但凭据没配齐**：前台根本不展示该渠道，运营却以为「已经开了」；
3. **管理端摘要 ≠ 下单链路**：两处若各自维护必需项清单，会出现
   「后台显示可用、用户点支付报 400」——本文件钉住它们来自同一份判断。
"""

import unittest
from unittest import mock

from sqlalchemy import select

from app.config import settings
from app.core import crypto
from app.core.rbac import match_route_permission
from app.models.system_setting import SystemSetting
from app.services import payment_settings_service, setting_service
from app.services.integration_settings_service import SECRET_KEYS, IntegrationConfig
from app.services.payments import factory
from app.services.setting_service import (
    SURFACE_LEGAL,
    SURFACE_LOGIN,
    SURFACE_PAYMENT,
    SURFACE_SETTINGS,
)
from tests.fake_db import MemorySession


def _config(**overrides) -> IntegrationConfig:
    """构造集成配置快照；密钥类字段写密文（与库里一致，secret() 会解密）"""
    values: dict[str, str] = {}
    for key, raw in overrides.items():
        values[key] = crypto.encrypt_secret(raw) if key in SECRET_KEYS else raw
    return IntegrationConfig(values)


def _full_wechat() -> dict:
    return {
        "wechat_pay_enabled": "1",
        "wechat_pay_mch_id": "1900000001",
        "wechat_pay_appid": "wx1234567890",
        "wechat_pay_serial_no": "SERIAL123",
        "wechat_pay_api_v3_key": "k" * 32,
        "wechat_pay_private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        "wechat_pay_platform_public_key": "-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----",
    }


def _full_alipay() -> dict:
    return {
        "alipay_enabled": "1",
        "alipay_app_id": "2021000000000000",
        "alipay_private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        "alipay_public_key": "-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----",
    }


class PaymentScopeTests(unittest.TestCase):
    """① 归属与键集合"""

    def test_payment_keys_are_not_in_other_surfaces(self):
        others = (
            setting_service.keys_for_surface(SURFACE_SETTINGS)
            | setting_service.keys_for_surface(SURFACE_LOGIN)
            | setting_service.keys_for_surface(SURFACE_LEGAL)
        )
        leaked = payment_settings_service.PAYMENT_SETTING_KEYS & others
        self.assertEqual(leaked, set(), f"支付键同时归属其它界面（会出现双写）: {sorted(leaked)}")

    def test_settings_payload_excludes_payment_groups(self):
        groups = {g["key"] for g in setting_service.groups_payload(surface=SURFACE_SETTINGS)}
        self.assertFalse(
            {g for g in groups if g.startswith("payment")}, f"系统设置仍在下发支付分组: {groups}"
        )

    def test_groups_and_counts(self):
        self.assertEqual(
            [g["key"] for g in payment_settings_service.groups()],
            ["payment_general", "payment_wechat", "payment_alipay"],
        )
        keys = payment_settings_service.PAYMENT_SETTING_KEYS
        self.assertEqual(len(keys), 18, "支付配置项数量变了，检查是否有键漏归属")
        self.assertIn("payment_enabled", keys)
        self.assertIn("wechat_pay_private_key", keys)
        self.assertIn("alipay_public_key", keys)


class PaymentWriteScopeTests(unittest.IsolatedAsyncioTestCase):
    """② 写通道只能写支付键"""

    async def asyncSetUp(self):
        self.db = MemorySession()

    async def _rows(self):
        return {
            r.setting_key: r.value
            for r in (await self.db.execute(select(SystemSetting))).scalars().all()
        }

    async def test_accepts_payment_keys(self):
        changed = await payment_settings_service.update(self.db, {"payment_enabled": "1"})
        await self.db.commit()
        self.assertEqual(changed, 1)

    async def test_rejects_foreign_keys(self):
        for key in ("cos_secret_key", "phone_login_enabled", "legal_privacy", "site_name"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    await payment_settings_service.update(
                        self.db, {"payment_enabled": "1", key: "injected"}
                    )
        self.assertEqual(await self._rows(), {}, "越界写入没有被整批放弃")

    async def test_secret_is_encrypted_and_masked(self):
        await payment_settings_service.update(self.db, {"wechat_pay_private_key": "PEM-PLAIN"})
        await self.db.commit()
        stored = (await self._rows())["wechat_pay_private_key"]
        self.assertTrue(crypto.is_encrypted(stored))
        values = await payment_settings_service.stored_values(self.db)
        self.assertTrue(values["wechat_pay_private_key"].startswith(crypto.MASK_PREFIX))


class ChannelOverviewTests(unittest.TestCase):
    """③ 渠道可用性与风险提示"""

    def _channels(self, config):
        return {c["key"]: c for c in payment_settings_service.channel_overview(config)}

    def test_all_disabled(self):
        # 注意：Mock 的默认值是 "1"（本地联调方便），所以要显式关掉再断言「全关」状态
        channels = self._channels(_config(payment_mock_enabled="0"))
        for key in ("mock", "wechat", "alipay"):
            with self.subTest(channel=key):
                self.assertFalse(channels[key]["enabled"])
                self.assertFalse(channels[key]["ready"])
                self.assertEqual(channels[key]["issues"], [])

    def test_mock_is_ready_only_outside_production(self):
        config = _config(payment_enabled="1", payment_mock_enabled="1")
        with mock.patch.object(settings, "APP_ENV", "development"):
            mock_channel = self._channels(config)["mock"]
            self.assertTrue(mock_channel["ready"])
            self.assertTrue(mock_channel["issues"], "Mock 可用时必须给出风险提示")
        with mock.patch.object(settings, "APP_ENV", "production"):
            mock_channel = self._channels(config)["mock"]
            self.assertFalse(mock_channel["ready"], "生产环境绝不能把 Mock 判为可用")
            self.assertTrue(mock_channel["issues"], "开关开着但被环境拒绝，也要说明为什么")

    def test_channel_blocked_by_master_switch(self):
        """总开关关闭时，即便渠道配齐也不能算可用（与下单链路同口径）"""
        config = _config(payment_mock_enabled="0", **_full_wechat())
        channel = self._channels(config)["wechat"]
        self.assertTrue(channel["enabled"])
        self.assertFalse(channel["ready"])
        self.assertTrue(any("总开关" in issue for issue in channel["issues"]))

    def test_wechat_requires_all_credentials(self):
        partial = _config(payment_enabled="1", **{**_full_wechat(), "wechat_pay_private_key": ""})
        channel = self._channels(partial)["wechat"]
        self.assertTrue(channel["enabled"])
        self.assertFalse(channel["ready"])
        self.assertTrue(channel["issues"])

        channel = self._channels(_config(payment_enabled="1", **_full_wechat()))["wechat"]
        self.assertTrue(channel["ready"])
        self.assertEqual(channel["issues"], [])

    def test_alipay_requires_all_credentials(self):
        partial = _config(payment_enabled="1", **{**_full_alipay(), "alipay_public_key": ""})
        self.assertFalse(self._channels(partial)["alipay"]["ready"])
        self.assertTrue(self._channels(_config(payment_enabled="1", **_full_alipay()))["alipay"]["ready"])

    def test_overview_matches_order_channel_list(self):
        """管理端摘要与下单可用渠道必须同一份判断（否则「后台可用、下单报错」）"""
        cases = [
            _config(),
            _config(payment_enabled="1", payment_mock_enabled="1"),
            _config(payment_enabled="1", **_full_wechat()),
            _config(payment_enabled="1", **_full_alipay()),
            _config(payment_enabled="1", **_full_wechat(), **_full_alipay()),
        ]
        with mock.patch.object(settings, "APP_ENV", "development"):
            for config in cases:
                with self.subTest(config=sorted(k for k, v in config.values.items() if v == "1")):
                    ready = {
                        c["key"] for c in payment_settings_service.channel_overview(config) if c["ready"]
                    }
                    orderable = {c["value"] for c in factory.available_channels(config)}
                    self.assertEqual(ready, orderable)

    def test_warnings_cover_the_three_silent_failures(self):
        # 1) 总开关关闭：正常运营选择，info
        warnings = payment_settings_service.warnings_of(
            _config(), payment_settings_service.channel_overview(_config())
        )
        self.assertEqual([w["level"] for w in warnings], ["info"])

        # 2) Mock 真实可用：danger
        config = _config(payment_enabled="1", payment_mock_enabled="1")
        with mock.patch.object(settings, "APP_ENV", "development"):
            warnings = payment_settings_service.warnings_of(
                config, payment_settings_service.channel_overview(config)
            )
        self.assertIn("danger", [w["level"] for w in warnings])

        # 3) 总开关开着但没有任何可用渠道：warn
        config = _config(payment_enabled="1", payment_mock_enabled="0")
        warnings = payment_settings_service.warnings_of(
            config, payment_settings_service.channel_overview(config)
        )
        self.assertIn("warn", [w["level"] for w in warnings])

    def test_no_credentials_leak(self):
        secret_private = "PRIVATE-KEY-DO-NOT-LEAK"
        config = _config(**{**_full_wechat(), "wechat_pay_private_key": secret_private})
        blob = repr(payment_settings_service.channel_overview(config)) + repr(
            payment_settings_service.warnings_of(config, payment_settings_service.channel_overview(config))
        )
        self.assertNotIn(secret_private, blob)


class PaymentRouteGuardTests(unittest.TestCase):
    def test_route_permissions(self):
        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/payment-settings"),
            ["payment_setting:view"],
        )
        self.assertEqual(
            match_route_permission("PUT", "/api/v1/admin/payment-settings"),
            ["payment_setting:update"],
        )

    def test_endpoint_registered(self):
        from app.main import app

        self.assertIn("/api/v1/admin/payment-settings", app.openapi()["paths"])

    def test_order_endpoints_untouched(self):
        """配置与业务数据分开：套餐/订单接口仍在（本模块没有把它们挪走）"""
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/api/v1/admin/payment-products", paths)
        self.assertIn("/api/v1/admin/payment-orders", paths)


if __name__ == "__main__":
    unittest.main()
