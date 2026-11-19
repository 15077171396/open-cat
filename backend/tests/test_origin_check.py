"""写请求 Origin 校验的单元测试：纯函数判定 + ASGI 中间件端到端（零网络）。"""

import unittest

from app.core.origin_check import OriginCheckMiddleware, _origin_allowed

_ALLOW = ["http://localhost:5173", "https://admin.example.com"]


class OriginAllowedTests(unittest.TestCase):
    def test_same_host_allowed(self):
        self.assertTrue(_origin_allowed("https://my-site.com", "my-site.com", _ALLOW))
        # 反代透传 Host：Origin 带端口、Host 带端口，完全一致才算同源
        self.assertTrue(_origin_allowed("http://127.0.0.1:8001", "127.0.0.1:8001", _ALLOW))

    def test_allowlist_allowed(self):
        self.assertTrue(_origin_allowed("http://localhost:5173", "api.example.com", _ALLOW))
        self.assertTrue(_origin_allowed("https://admin.example.com/", "api.example.com", _ALLOW))

    def test_cross_site_denied(self):
        self.assertFalse(_origin_allowed("https://evil.example", "my-site.com", _ALLOW))
        self.assertFalse(_origin_allowed("http://localhost:9999", "my-site.com", _ALLOW), "同 host 不同端口不算同源")

    def test_malformed_origin_denied(self):
        self.assertFalse(_origin_allowed("not-a-url", "my-site.com", _ALLOW))
        self.assertFalse(_origin_allowed("file://", "my-site.com", _ALLOW))
        self.assertFalse(_origin_allowed("", "my-site.com", _ALLOW))


def _scope(method: str, origin: str = "", host: str = "my-site.com") -> dict:
    headers = []
    if origin:
        headers.append((b"origin", origin.encode()))
    headers.append((b"host", host.encode()))
    return {"type": "http", "method": method, "headers": headers}


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, calls: list):
        async def inner(scope, receive, send):
            calls.append(scope["method"])
        return inner

    async def test_unsafe_method_with_evil_origin_is_403(self):
        calls: list = []
        sent: list = []

        async def _send(event):
            sent.append(event)

        mw = OriginCheckMiddleware(self._app(calls), allowlist=_ALLOW)
        await mw(_scope("DELETE", origin="https://evil.example"), None, _send)
        self.assertEqual(calls, [], "恶意来源不得触达业务处理")
        self.assertEqual(sent[0]["status"], 403)

    async def test_safe_methods_pass_through_without_origin(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            calls: list = []
            mw = OriginCheckMiddleware(self._app(calls), allowlist=_ALLOW)
            await mw(_scope(method), None, lambda _e: None)
            self.assertEqual(calls, [method], f"{method} 不该被 Origin 校验拦截")

    async def test_unsafe_method_without_origin_passes(self):
        """无 Origin 头 = 机器客户端（curl / PAT）—— 开放 API 的一等公民，放行。"""
        calls: list = []
        mw = OriginCheckMiddleware(self._app(calls), allowlist=_ALLOW)
        await mw(_scope("POST"), None, lambda _e: None)
        self.assertEqual(calls, ["POST"])

    async def test_unsafe_method_same_origin_passes(self):
        calls: list = []
        mw = OriginCheckMiddleware(self._app(calls), allowlist=_ALLOW)
        await mw(_scope("POST", origin="http://my-site.com"), None, lambda _e: None)
        self.assertEqual(calls, ["POST"])


if __name__ == "__main__":
    unittest.main()
