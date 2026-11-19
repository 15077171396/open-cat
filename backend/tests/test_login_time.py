"""登录时段限制契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **fail-open**：配置为空 / 解析失败一律按「不限制」处理 —— 一条手滑的
   坏配置把全站锁死比不限制严重得多。
2. **跨天窗口**：22:00-06:00 表示夜里 22 点到次日 6 点（两端都含），
   这是运维写时段时最自然的表达，解析错了等于没限制。
3. **闸门最靠前**：时段拒绝发生在验证码 / 密码校验之前（快速失败、
   不泄露账号存在性），且拒绝同样进登录日志。
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.config import settings
from app.core.exceptions import AppException
from app.core.login_time import check_login_time, is_login_allowed, parse_window
from app.services import auth_service

# 北京时间构造器：hour 为北京时间小时
_CST = timezone(timedelta(hours=8))


def _cst(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 19, hour, minute, tzinfo=_CST)


class ParseTests(unittest.TestCase):
    def test_parses_normal_window(self):
        self.assertEqual(parse_window("09:00-18:00"), (540, 1080))

    def test_parses_overnight_window(self):
        self.assertEqual(parse_window("22:00-06:00"), (1320, 360))

    def test_empty_means_no_limit(self):
        self.assertIsNone(parse_window(""))
        self.assertIsNone(parse_window(None))
        self.assertIsNone(parse_window("   "))

    def test_invalid_formats_return_none(self):
        for bad in ("25:00-06:00", "9:00", "09:00-6:0", "abc", "09:00~18:00"):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_window(bad), f"非法格式 {bad} 必须被拒绝")

    def test_endpoints_are_inclusive(self):
        self.assertEqual(parse_window("00:00-23:59"), (0, 1439))


class AllowTests(unittest.TestCase):
    def setUp(self):
        self._patcher = mock.patch.object(settings, "LOGIN_TIME_WINDOW", "09:00-18:00")
        self._patcher.start()
        is_login_allowed(_cst(12))  # 触发一次重载缓存

    def tearDown(self):
        self._patcher.stop()
        is_login_allowed(_cst(12))  # 清缓存，避免污染其他用例

    def test_inside_window_allowed(self):
        self.assertTrue(is_login_allowed(_cst(9)))
        self.assertTrue(is_login_allowed(_cst(12, 30)))
        self.assertTrue(is_login_allowed(_cst(18)))

    def test_outside_window_blocked(self):
        self.assertFalse(is_login_allowed(_cst(8, 59)))
        self.assertFalse(is_login_allowed(_cst(18, 1)))
        self.assertFalse(is_login_allowed(_cst(3)))

    def test_no_config_means_allowed(self):
        with mock.patch.object(settings, "LOGIN_TIME_WINDOW", ""):
            self.assertTrue(is_login_allowed(_cst(3)))
        with mock.patch.object(settings, "LOGIN_TIME_WINDOW", "25:00-99:99"):
            # fail-open：解析失败按不限制处理（坏配置不能锁死全站）
            self.assertTrue(is_login_allowed(_cst(3)))

    def test_overnight_window(self):
        with mock.patch.object(settings, "LOGIN_TIME_WINDOW", "22:00-06:00"):
            is_login_allowed(_cst(12))  # 重载缓存
            self.assertTrue(is_login_allowed(_cst(23)))
            self.assertTrue(is_login_allowed(_cst(2)))
            self.assertTrue(is_login_allowed(_cst(6)))
            self.assertFalse(is_login_allowed(_cst(12)))


class GateTests(unittest.TestCase):
    def test_check_raises_outside_window_with_friendly_message(self):
        with mock.patch.object(settings, "LOGIN_TIME_WINDOW", "09:00-18:00"):
            with self.assertRaises(AppException) as ctx:
                check_login_time(_cst(3))
        self.assertEqual(ctx.exception.code, 403)
        self.assertIn("09:00-18:00", ctx.exception.message)

    def test_check_passes_inside_window(self):
        with mock.patch.object(settings, "LOGIN_TIME_WINDOW", "09:00-18:00"):
            check_login_time(_cst(12))  # 不抛即通过

    def test_login_records_time_block_in_login_log(self):
        """时段拒绝发生在验证码之前，且进登录日志（status=failed）"""

        class _Req:
            account = "13812345678"
            password = "pw"
            captcha_id = None
            captcha_code = None
            totp_code = None

        with mock.patch.object(settings, "LOGIN_TIME_WINDOW", "09:00-18:00"):
            with mock.patch.object(
                auth_service,
                "check_login_time",
                mock.Mock(side_effect=AppException(code=403, message="当前时段不允许登录")),
            ):
                with mock.patch.object(
                    auth_service.login_log_service, "record", new=mock.AsyncMock()
                ) as rec:
                    with self.assertRaises(AppException) as ctx:
                        _run_login(_Req())
        self.assertIn("时段", ctx.exception.message)
        # 拒绝同样进登录日志：排查「为什么大家都登不上」时的唯一线索
        rec.assert_awaited_once()
        kwargs = rec.await_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertIn("时段", kwargs["message"])


def _run_login(req):
    return asyncio.run(auth_service.login(req, None, client_ip="1.2.3.4", user_agent="UA"))


if __name__ == "__main__":
    unittest.main()
