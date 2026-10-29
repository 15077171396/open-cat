"""5xx 业务异常必须留痕（第三轮增量审查 2026-10-11 P1-49②）。

背景
----
`app_exception_handler` 此前**完全不记日志**，尽管同处注释写着「502/503 等有效 5xx
保留原值（区分上游故障与内部错误，利于告警分级）」—— 于是第三方资料导入失败（502）、
AI 提供方故障这类**系统性**问题在 app.log 里零痕迹，只能靠用户反馈才知道线上在报错。

本文件钉住两条：
1. `AppException(code>=500)` 必须写一条 ERROR 日志（含 path / method / status）；
2. 4xx 不记（用户输入问题，逐条记录会把日志刷满 —— 与 `log_redis_degraded`
   同一个取舍），避免「修好可观测性反而把日志挤掉」。
"""

import unittest

import httpx
from fastapi import FastAPI
from loguru import logger

from app.core.exceptions import AppException, register_exception_handlers


class _LogCapture:
    """收集 loguru 记录（按 level 过滤，用完移除 sink）"""

    def __init__(self, level: str = "ERROR"):
        self.records: list = []
        self._sink_id = logger.add(
            lambda message: self.records.append(message.record), level=level
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        logger.remove(self._sink_id)
        return False

    def events(self, name: str) -> list:
        return [r for r in self.records if r["extra"].get("event") == name]


def _app_raising(code: int) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def _boom():
        raise AppException(code=code, message="上游服务异常，请稍后重试")

    return app


class AppExceptionLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, code: int):
        app = _app_raising(code)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/boom")


class FiveHundredLoggingTests(AppExceptionLoggingTests):
    async def test_5xx_is_logged_with_request_context(self):
        with _LogCapture() as cap:
            resp = await self._call(502)

        self.assertEqual(resp.status_code, 502)
        hits = cap.events("app_exception")
        self.assertEqual(len(hits), 1, "5xx 业务异常没有写日志")
        extra = hits[0]["extra"]
        self.assertEqual(extra["status"], 502)
        self.assertEqual(extra["path"], "/boom")
        self.assertEqual(extra["method"], "GET")

    async def test_4xx_is_not_logged(self):
        """4xx 是用户输入问题，不记 —— 否则日志会被刷满"""
        with _LogCapture() as cap:
            resp = await self._call(400)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(cap.events("app_exception"), [], "4xx 不该写日志")


if __name__ == "__main__":
    unittest.main()
