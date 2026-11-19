"""IP 黑名单契约测试。

钉住四条**破了不报错、只会悄悄失去意义**的契约：

1. **拉黑必须真的拦**：命中黑名单的请求吃 403（403 响应走统一信封），
   `203.0.113.0/24` 这类 CIDR 要能覆盖段内任意地址。
2. **健康检查永远放行**：探活被黑名单挡住 = 运维误判宕机，这条豁免不能丢。
3. **坏行不能让全站 500**：非法条目跳过并回传提示（fail-open 是刻意选择）。
4. **保存即时生效**：中间件按配置值指纹重载解析结果（改完配置不等重启）；
   保存动作与审计同事务提交（拉黑/放行都是改变线上访问面的操作）。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.config import settings
from app.core.exceptions import AppException
from app.core.ip_guard import IPBlacklistMiddleware, is_blocked, parse_blacklist
from app.services import ip_blacklist_service as ibs


def _run(coro):
    return asyncio.run(coro)


def _fake_db(existing_row=None):
    added: list = []
    deleted: list = []

    async def _call(*args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: existing_row)

    return (
        SimpleNamespace(execute=_call, add=added.append, delete=deleted.append,
                        commit=mock.AsyncMock()),
        added,
        deleted,
    )


class ParseTests(unittest.TestCase):
    def test_parse_accepts_ip_and_cidr(self):
        valid, invalid = parse_blacklist("203.0.113.7\n10.0.0.0/8, 2001:db8::/32")
        self.assertEqual(valid, ["203.0.113.7", "10.0.0.0/8", "2001:db8::/32"])
        self.assertEqual(invalid, [])

    def test_parse_reports_invalid_without_raising(self):
        valid, invalid = parse_blacklist("203.0.113.7\nnot-an-ip\n300.1.2.3")
        self.assertEqual(valid, ["203.0.113.7"])
        self.assertEqual(invalid, ["not-an-ip", "300.1.2.3"])

    def test_parse_empty_and_blank_lines(self):
        valid, invalid = parse_blacklist(None)
        self.assertEqual((valid, invalid), ([], []))
        valid, _ = parse_blacklist("  \n,\n  ")
        self.assertEqual(valid, [])


class BlockTests(unittest.TestCase):
    def setUp(self):
        self._patcher = mock.patch.object(settings, "IP_BLACKLIST", "203.0.113.0/24\n1.2.3.4")
        self._patcher.start()
        # 指纹缓存按值失效：直接触发一次重载
        is_blocked("9.9.9.9")

    def tearDown(self):
        self._patcher.stop()
        # 恢复后触发一次重载，避免污染其他用例
        is_blocked("9.9.9.9")

    def test_cidr_covers_whole_range(self):
        self.assertTrue(is_blocked("203.0.113.7"))
        self.assertTrue(is_blocked("203.0.113.200"))
        self.assertFalse(is_blocked("203.0.114.7"))

    def test_single_ip_and_non_listed_pass(self):
        self.assertTrue(is_blocked("1.2.3.4"))
        self.assertFalse(is_blocked("8.8.8.8"))

    def test_invalid_ip_argument_passes(self):
        self.assertFalse(is_blocked("not-an-ip"))
        self.assertFalse(is_blocked(""))


class MiddlewareTests(unittest.TestCase):
    def _drive(self, path: str, client_ip: str):
        """用假 ASGI 通道驱动中间件，返回响应状态码"""
        import json

        sent: list = []

        async def _send(message):
            sent.append(message)

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        captured = {}

        async def _app(scope, receive, send):
            captured["called"] = True
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"x-forwarded-for", client_ip.encode())],
            "query_string": b"",
        }
        # get_client_ip 依赖 TRUSTED_PROXIES：测试里直连形态取 scope["client"]
        scope["client"] = (client_ip, 12345)

        mw = IPBlacklistMiddleware(_app)
        _run(mw(scope, _receive, _send))
        status = None
        for message in sent:
            if message["type"] == "http.response.start":
                status = message["status"]
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        # 业务 app 的响应体不是 JSON（b"ok"）：只有 403 信封才解析
        parsed = None
        if body and status == 403:
            parsed = json.loads(body)
        return captured.get("called", False), status, parsed

    def test_blocked_ip_gets_403_envelope(self):
        with mock.patch.object(settings, "IP_BLACKLIST", "203.0.113.0/24"):
            called, status, body = self._drive("/api/v1/admin/users", "203.0.113.9")
        self.assertFalse(called, "被拉黑的请求不该触达业务 app")
        self.assertEqual(status, 403)
        self.assertEqual(body["code"], 403)

    def test_health_check_is_exempt(self):
        with mock.patch.object(settings, "IP_BLACKLIST", "0.0.0.0/0"):
            called, status, _ = self._drive("/api/v1/health", "203.0.113.9")
        self.assertTrue(called, "健康检查不受黑名单影响")
        self.assertEqual(status, 200)

    def test_normal_ip_passes_through(self):
        with mock.patch.object(settings, "IP_BLACKLIST", "203.0.113.0/24"):
            called, status, _ = self._drive("/api/v1/admin/users", "8.8.8.8")
        self.assertTrue(called)
        self.assertEqual(status, 200)


class SaveTests(unittest.TestCase):
    def test_save_creates_row_and_commits_with_audit(self):
        db, added, _ = _fake_db(existing_row=None)
        admin = SimpleNamespace(id=1, account="root")
        with mock.patch.object(settings, "IP_BLACKLIST", ""):
            with mock.patch("app.services.admin_audit_service.record_admin_action") as audit:
                with mock.patch.object(ibs, "apply_runtime_settings", new=mock.AsyncMock()):
                    with mock.patch.object(ibs, "invalidate_runtime_settings_cache", new=mock.AsyncMock()):
                        result = _run(ibs.save_with_audit(db, admin, "203.0.113.7\n10.0.0.0/8\n坏行"))
        self.assertTrue(result["changed"])
        self.assertEqual(len(added), 1)
        self.assertEqual(len(result["invalid_entries"]), 1)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[2], "ip_blacklist_update")
        db.commit.assert_awaited_once()

    def test_save_empty_deletes_row(self):
        row = SimpleNamespace(setting_key=ibs.BLACKLIST_SETTING_KEY, value="1.2.3.4")
        db, added, deleted = _fake_db(existing_row=row)
        admin = SimpleNamespace(id=1, account="root")
        with mock.patch.object(settings, "IP_BLACKLIST", "1.2.3.4"):
            with mock.patch("app.services.admin_audit_service.record_admin_action") as audit:
                result = _run(ibs.save_with_audit(db, admin, ""))
        self.assertTrue(result["changed"])
        self.assertEqual(len(deleted), 1)
        audit.assert_called_once()

    def test_save_rejects_over_limit(self):
        db, _, _ = _fake_db(existing_row=None)
        admin = SimpleNamespace(id=1, account="root")
        huge = "\n".join(f"10.0.{i // 256}.{i % 256}" for i in range(ibs.MAX_ENTRIES + 1))
        with self.assertRaises(AppException):
            _run(ibs.save_with_audit(db, admin, huge))


class IpBlacklistApiTests(unittest.TestCase):
    def test_get_endpoint_shape(self):
        from tests.conftest import async_client
        from app.models.user import User

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                return await client.get("/api/v1/admin/ip-blacklist")

        resp = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertIn("entries", body["data"])
        self.assertIn("invalid_entries", body["data"])

    def test_put_endpoint_delegates_to_service(self):
        from tests.conftest import async_client
        from app.models.user import User
        from app.api import admin_ip_blacklist

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                with mock.patch.object(
                    ibs,
                    "save_with_audit",
                    new=mock.AsyncMock(
                        return_value={
                            "changed": True,
                            "entries": ["1.2.3.4"],
                            "invalid_entries": [],
                            "added": ["1.2.3.4"],
                            "removed": [],
                        }
                    ),
                ) as save:
                    resp = await client.put(
                        "/api/v1/admin/ip-blacklist", json={"raw_text": "1.2.3.4"}
                    )
            return resp, save

        resp, save = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["changed"], True)
        save.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
