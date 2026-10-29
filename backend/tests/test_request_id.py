"""请求关联 ID 回归测试（第三轮增量审查 P1-34）。

为什么需要：用户报「14:32 页面 500」时，没有关联 ID 就无法把 nginx access
log、应用日志、Sentry 事件串成一条线索（gunicorn `-w 2` 请求日志交错）。

本文件钉住四件事：
1. 中间件在无入站头时**生成**、有合法入站头时**透传**，并统一在响应头回显；
2. 入站头是攻击者可控的 —— 富字符/超长一律**丢弃重新生成**（防日志注入）；
3. 日志 filter 把 request_id 注入每一条日志的 extra（无需逐处 logger.bind）；
4. 接线守卫：`create_app()` 真的挂载了该中间件（最外层）、nginx 真的注入了
   该请求头、响应头真的暴露给浏览器（CORS expose_headers）。
"""

import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.core.request_context import (
    RequestIdMiddleware,
    get_request_id,
    new_request_id,
    sanitize_request_id,
)

_HEX32 = 32


def _build_app():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/probe")
    async def probe():
        # 端点内读到的必须是中间件写入的同一个 ID（contextvar 贯通调用链）
        return {"request_id": get_request_id()}

    return app


async def _call(headers: dict | None = None):
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/probe", headers=headers or {})
    return resp


class RequestIdMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_generates_and_echoes_when_header_absent(self):
        resp = await _call()
        rid = resp.headers.get("x-request-id")
        self.assertIsNotNone(rid, "响应必须回显 X-Request-Id")
        self.assertEqual(len(rid), _HEX32)
        self.assertEqual(resp.json()["request_id"], rid, "端点内读到的 ID 与响应头必须一致")

    async def test_valid_incoming_header_is_reused(self):
        """nginx `$request_id` 注入的头必须原样透传，否则 nginx 与应用无法对齐"""
        resp = await _call(headers={"X-Request-Id": "abc123-._DEF"})
        self.assertEqual(resp.headers.get("x-request-id"), "abc123-._DEF")
        self.assertEqual(resp.json()["request_id"], "abc123-._DEF")

    async def test_dangerous_incoming_header_is_replaced(self):
        """富字符 / 空格 / 超长一律重新生成 —— 入站头可被用于日志注入"""
        # 非 ASCII 用 bytes 发送（httpx 不允许 str 携带非 ASCII；覆盖解码失败分支）
        for bad in ("a" * 65, "has space", "<script>", "a;rm -rf", "a\nb", b"caf\xe9"):
            with self.subTest(bad=bad):
                resp = await _call(headers={"X-Request-Id": bad})
                rid = resp.headers.get("x-request-id")
                self.assertNotEqual(rid, bad, f"非法头 {bad!r} 被原样透传了")
                self.assertEqual(len(rid), _HEX32, "非法头必须替换为重新生成的 hex ID")

    async def test_context_is_cleared_after_request(self):
        await _call()
        self.assertIsNone(get_request_id(), "请求结束后必须归还 contextvar，避免串号")

    def test_sanitize_helper(self):
        self.assertEqual(sanitize_request_id("ok-1"), "ok-1")
        self.assertIsNone(sanitize_request_id(None))
        self.assertIsNone(sanitize_request_id(""))
        self.assertIsNone(sanitize_request_id("x" * 65))
        self.assertEqual(len(new_request_id()), _HEX32)


class RequestIdLogFilterTests(unittest.IsolatedAsyncioTestCase):
    """日志 filter：request_id 注入每条日志的 extra（两个 sink 共用同一 filter）"""

    async def test_log_record_carries_request_id(self):
        from loguru import logger

        from app.logging import _inject_request_id

        captured: list[dict] = []

        def _sink(message):
            captured.append(dict(message.record["extra"]))

        sink_id = logger.add(_sink, level="INFO", filter=_inject_request_id, format="{message}")
        try:
            app = FastAPI()
            app.add_middleware(RequestIdMiddleware)

            @app.get("/log")
            async def _log():
                logger.info("probe log")
                return {"ok": True}

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/log", headers={"X-Request-Id": "rid-for-log"})
        finally:
            logger.remove(sink_id)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(captured, "sink 未捕获到日志，断言会变成空断言")
        self.assertEqual(captured[-1].get("request_id"), "rid-for-log")

    async def test_explicit_bind_wins_over_context(self):
        """调用方显式 bind(request_id=...) 时以调用方为准（setdefault 语义）"""
        from loguru import logger

        from app.logging import _inject_request_id

        captured: list[dict] = []

        def _sink(message):
            captured.append(dict(message.record["extra"]))

        sink_id = logger.add(_sink, level="INFO", filter=_inject_request_id, format="{message}")
        try:
            logger.bind(request_id="caller-explicit").info("probe")
        finally:
            logger.remove(sink_id)

        self.assertEqual(captured[-1].get("request_id"), "caller-explicit")

    def test_records_without_request_context_get_placeholder(self):
        """启动 / 脚本 / 调度器阶段无请求上下文，格式串里的 {extra[request_id]} 仍须有值"""
        from app.logging import _inject_request_id

        record = {"extra": {}}
        self.assertTrue(_inject_request_id(record))
        self.assertEqual(record["extra"]["request_id"], "-")


class RequestIdWiringTests(unittest.TestCase):
    """接线守卫：中间件真的挂上了、且在最外层（429/500 也要带上关联 ID）"""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.app = app

    def test_middleware_registered_outermost(self):
        names = [m.cls.__name__ for m in self.app.user_middleware]
        self.assertIn("RequestIdMiddleware", names, "create_app() 未挂载 RequestIdMiddleware")
        self.assertEqual(
            names[0],
            "RequestIdMiddleware",
            "RequestIdMiddleware 必须最外层（最后 add），否则限流 429 等响应带不上关联 ID",
        )

    def test_cors_exposes_request_id_header(self):
        cors = next(m for m in self.app.user_middleware if m.cls.__name__ == "CORSMiddleware")
        self.assertIn(
            "X-Request-Id",
            cors.kwargs.get("expose_headers") or [],
            "未 exposed 时浏览器 JS 读不到关联 ID，前端 Sentry 无法与之关联",
        )

    def test_nginx_injects_request_id_for_backend_proxies(self):
        nginx = (Path(__file__).resolve().parents[2] / "nginx.conf").read_text(encoding="utf-8")
        # /api/ + sitemap.xml + sitemap 分片 + llms.txt + IndexNow = 5 处后端代理
        self.assertGreaterEqual(
            nginx.count("proxy_set_header X-Request-Id $request_id;"),
            5,
            "nginx 后端代理未完整注入 X-Request-Id（应用侧将只能自造 ID，与 access log 对不上）",
        )


if __name__ == "__main__":
    unittest.main()