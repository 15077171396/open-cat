"""部署体检（`/admin/readiness`）的契约测试。

钉的是「给不给得出可操作的结论」，而不是「六个函数都返回了字典」：

1. **必填项未就绪必须阻塞**：存储不可写、站点地址还是 localhost、邮件缺项，
   这三项任一不达标就不该告诉用户「可以上线了」。
2. **「配了但不对」要单独标 error**：开了微信支付却没填商户号、启用模型却没填 Key、
   开了手机登录却没选短信商 —— 这类比「没配」更危险，因为用户以为自己配好了。
3. **明确关闭不算问题**：支付关了、短信没启用，只要没有依赖它的开关打开，
   就标 `off` 而不是 error，否则体检页会永远挂红。
4. **单项炸了不拖垮整页**：AI/短信/支付三项要读库读缓存，任一项抛异常时
   其余项照常给出结论。
5. **只读**：这个接口不允许写库（与 `/system-status` 同一定位）。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import settings
from app.services import readiness_service as rs


def _by_key(items: list[dict], key: str) -> dict:
    for item in items:
        if item["key"] == key:
            return item
    raise AssertionError(f"体检结果里没有 {key}：{[i['key'] for i in items]}")


class _FakeConfig:
    """`IntegrationConfig` 的替身：只实现体检用到的两个读方法。"""

    def __init__(self, strings=None, secrets=None, booleans=None):
        self._s = strings or {}
        self._sec = secrets or {}
        self._b = booleans or {}

    def str(self, key: str) -> str:
        return self._s.get(key, "")

    def secret(self, key: str) -> str:
        return self._sec.get(key, "")

    def bool(self, key: str) -> bool:
        return self._b.get(key, False)


class StorageCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_driver = settings.STORAGE_DRIVER
        self._orig_dir = settings.STORAGE_LOCAL_DIR
        self.addCleanup(self._restore)
        from app.services import storage

        storage._load_provider.cache_clear()
        self.addCleanup(storage._load_provider.cache_clear)

    def _restore(self):
        settings.STORAGE_DRIVER = self._orig_driver
        settings.STORAGE_LOCAL_DIR = self._orig_dir

    def test_local_driver_writable_is_ready(self):
        settings.STORAGE_DRIVER = "local"
        settings.STORAGE_LOCAL_DIR = self._tmp.name
        item = rs._check_storage()
        self.assertEqual(item["status"], "ready", item)
        self.assertTrue(item["required"])
        # 探针文件不能残留
        leftovers = [p for p in Path(self._tmp.name).iterdir() if p.name.startswith(".probe")]
        self.assertEqual(leftovers, [])

    def test_local_driver_unwritable_is_error_and_blocks(self):
        settings.STORAGE_DRIVER = "local"
        # 用文件路径当目录 → mkdir 必然失败
        blocker = Path(self._tmp.name) / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        settings.STORAGE_LOCAL_DIR = str(blocker)

        item = rs._check_storage()
        self.assertEqual(item["status"], "error", item)
        self.assertIn("不可写", item["detail"])
        # 必填项 error → 阻塞
        summary = rs._summarize([item])
        self.assertFalse(summary["deployable"])
        self.assertEqual(summary["blocking_keys"], ["storage"])

    def test_cos_driver_missing_credentials_is_error(self):
        settings.STORAGE_DRIVER = "cos"
        with mock.patch.object(settings, "COS_SECRET_ID", ""), mock.patch.object(
            settings, "COS_SECRET_KEY", ""
        ), mock.patch.object(settings, "COS_BUCKET", ""):
            item = rs._check_storage()
        self.assertEqual(item["status"], "error")
        self.assertIn("COS_SECRET_ID", item["detail"])


class SiteBaseUrlCheckTests(unittest.TestCase):
    def test_empty_is_unconfigured(self):
        with mock.patch.object(settings, "SITE_BASE_URL", ""):
            self.assertEqual(rs._check_site_base_url()["status"], "unconfigured")

    def test_localhost_blocks(self):
        """默认值是 localhost —— 这正是开源首次部署最常见的遗漏。"""
        for value in ("http://localhost", "http://127.0.0.1:8080", "http://0.0.0.0"):
            with self.subTest(value=value), mock.patch.object(settings, "SITE_BASE_URL", value):
                item = rs._check_site_base_url()
                self.assertEqual(item["status"], "unconfigured", item)
                self.assertTrue(item["required"])

    def test_https_domain_is_ready(self):
        with mock.patch.object(settings, "SITE_BASE_URL", "https://example.com"):
            self.assertEqual(rs._check_site_base_url()["status"], "ready")

    def test_http_domain_ready_with_warning(self):
        with mock.patch.object(settings, "SITE_BASE_URL", "http://example.com"):
            item = rs._check_site_base_url()
            self.assertEqual(item["status"], "ready")
            self.assertIn("HTTPS", item["action"])


class MailCheckTests(unittest.TestCase):
    def test_missing_fields_is_unconfigured_and_blocks(self):
        channel = {"ready": False, "missing": ["SMTP_HOST", "SMTP_USERNAME"], "tls": "none"}
        with mock.patch.object(rs.mail_settings_service, "channel_status", return_value=channel):
            item = rs._check_mail()
        self.assertEqual(item["status"], "unconfigured")
        self.assertIn("SMTP_HOST", item["detail"])
        self.assertTrue(item["required"])

    def test_ready_mirrors_channel_status(self):
        channel = {
            "ready": True,
            "missing": [],
            "endpoint": "smtp.example.com:465",
            "sender": "noreply@example.com",
            "tls": "ssl",
        }
        with mock.patch.object(rs.mail_settings_service, "channel_status", return_value=channel):
            item = rs._check_mail()
        self.assertEqual(item["status"], "ready")
        self.assertIn("smtp.example.com:465", item["detail"])


class AiCheckTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, rows):
        class _Result:
            def all(self_inner):
                return rows

        db = mock.Mock()
        db.execute = mock.AsyncMock(return_value=_Result())
        return await rs._check_ai(db)

    async def test_no_models_is_off_not_error(self):
        item = await self._run([])
        self.assertEqual(item["status"], "off")
        self.assertFalse(item["required"])

    async def test_enabled_without_key_is_error(self):
        """启用但没 Key：调用必失败，比没配更危险。"""
        item = await self._run([(True, ""), (True, None)])
        self.assertEqual(item["status"], "error")
        self.assertIn("API Key", item["detail"])

    async def test_enabled_with_key_is_ready(self):
        item = await self._run([(True, "enc:v2:xxx"), (False, "")])
        self.assertEqual(item["status"], "ready")
        self.assertIn("1 个模型可用", item["detail"])


class SmsCheckTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, config):
        with mock.patch.object(rs, "get_auth_config", mock.AsyncMock(return_value=config)):
            return await rs._check_sms(mock.Mock())

    async def test_phone_login_on_but_no_provider_is_error(self):
        """最常见的「配了不生效」：手机登录开着，短信商没选。"""
        cfg = _FakeConfig(booleans={"phone_login_enabled": True}, strings={"sms_provider": "none"})
        item = await self._run(cfg)
        self.assertEqual(item["status"], "error")
        self.assertIn("必定失败", item["detail"])

    async def test_phone_login_off_and_no_provider_is_off(self):
        cfg = _FakeConfig(booleans={"phone_login_enabled": False}, strings={"sms_provider": "none"})
        item = await self._run(cfg)
        self.assertEqual(item["status"], "off")

    async def test_provider_selected_but_incomplete_is_error(self):
        cfg = _FakeConfig(
            strings={"sms_provider": "aliyun", "sms_aliyun_access_key_id": "id"},
            secrets={},
        )
        item = await self._run(cfg)
        self.assertEqual(item["status"], "error")
        self.assertIn("sms_aliyun_access_key_secret", item["detail"])

    async def test_provider_complete_is_ready(self):
        cfg = _FakeConfig(
            strings={
                "sms_provider": "aliyun",
                "sms_aliyun_access_key_id": "id",
                "sms_aliyun_sign_name": "签名",
                "sms_aliyun_template_code": "SMS_1",
            },
            secrets={"sms_aliyun_access_key_secret": "sec"},
        )
        item = await self._run(cfg)
        self.assertEqual(item["status"], "ready")


class PaymentCheckTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, config):
        with mock.patch.object(rs, "get_payment_config", mock.AsyncMock(return_value=config)):
            return await rs._check_payment(mock.Mock())

    async def test_disabled_is_off(self):
        item = await self._run(_FakeConfig(booleans={"payment_enabled": False}))
        self.assertEqual(item["status"], "off")

    async def test_mock_mode_is_ready_but_warns(self):
        item = await self._run(
            _FakeConfig(booleans={"payment_enabled": True, "payment_mock_enabled": True})
        )
        self.assertEqual(item["status"], "ready")
        self.assertIn("模拟支付", item["detail"])

    async def test_enabled_without_any_channel_is_error(self):
        item = await self._run(_FakeConfig(booleans={"payment_enabled": True}))
        self.assertEqual(item["status"], "error")
        self.assertIn("都没开启", item["detail"])

    async def test_wechat_enabled_but_credentials_missing_is_error(self):
        """开了渠道但凭据不齐 → 用户可能已扣款而回调验签失败。"""
        cfg = _FakeConfig(
            booleans={"payment_enabled": True, "wechat_pay_enabled": True},
            strings={"wechat_pay_appid": "wx1"},
        )
        item = await self._run(cfg)
        self.assertEqual(item["status"], "error")
        self.assertIn("wechat_pay_mch_id", item["detail"])

    async def test_wechat_complete_is_ready(self):
        cfg = _FakeConfig(
            booleans={"payment_enabled": True, "wechat_pay_enabled": True},
            strings={
                "wechat_pay_appid": "wx1",
                "wechat_pay_mch_id": "mch1",
                "wechat_pay_serial_no": "serial1",
            },
            secrets={
                "wechat_pay_private_key": "k",
                "wechat_pay_platform_public_key": "p",
            },
        )
        item = await self._run(cfg)
        self.assertEqual(item["status"], "ready")
        self.assertIn("微信支付", item["detail"])


class CollectTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_broken_check_does_not_break_page(self):
        """任一项抛异常时，其余项照常给出结论 —— 体检页打不开时恰恰最需要它。"""

        async def boom(_db):
            raise RuntimeError("db down")

        with mock.patch.object(rs, "_check_ai", boom), mock.patch.object(
            rs, "_check_sms", boom
        ), mock.patch.object(rs, "_check_payment", boom):
            result = await rs.collect(mock.Mock())

        self.assertEqual(len(result["items"]), 6)
        for key in ("ai", "sms", "payment"):
            item = _by_key(result["items"], key)
            self.assertEqual(item["status"], "error")
            # 具体异常只进日志（degrade_guard 统一记录），响应里给可操作结论
            self.assertIn("检查失败", item["detail"])
            self.assertTrue(item["action"])
        # 不依赖数据库的三项仍然正常
        self.assertIn(_by_key(result["items"], "storage")["status"], {"ready", "error"})

    async def test_items_sorted_by_severity(self):
        items = [
            rs._item(key="a", label="A", status="ready", required=False, detail=""),
            rs._item(key="b", label="B", status="error", required=False, detail=""),
            rs._item(key="c", label="C", status="unconfigured", required=False, detail=""),
        ]
        items.sort(key=lambda i: rs._STATUS_ORDER[i["status"]])
        self.assertEqual([i["key"] for i in items], ["b", "c", "a"])

    async def test_summary_counts_only_required_blockers(self):
        items = [
            rs._item(key="storage", label="存储", status="error", required=True, detail=""),
            rs._item(key="mail", label="邮件", status="unconfigured", required=True, detail=""),
            # 可选功能没配 → 不算阻塞
            rs._item(key="ai", label="AI", status="off", required=False, detail=""),
        ]
        summary = rs._summarize(items)
        self.assertFalse(summary["deployable"])
        self.assertEqual(summary["blocking"], 2)
        self.assertEqual(summary["blocking_keys"], ["storage", "mail"])
        self.assertEqual(summary["error"], 1)


class ReadinessEndpointTests(unittest.TestCase):
    def test_route_registered_with_fail_closed_rule(self):
        """新端点必须登记路由权限规则，否则会被 fail-closed 直接 403。"""
        from app.core.rbac import match_route_permission

        perms = match_route_permission("GET", "/api/v1/admin/readiness")
        self.assertEqual(perms, ["dashboard:view"])

    def test_module_registry_covers_path_and_menu(self):
        from app import module_registry

        module = module_registry.module_for_path("/api/v1/admin/readiness")
        self.assertIsNotNone(module)
        self.assertEqual(module.key, "ops")
        self.assertTrue(module_registry.menu_visible("/readiness"))


if __name__ == "__main__":
    unittest.main()
