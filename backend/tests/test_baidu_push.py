"""百度 URL 推送的两条硬约束（缺口台账 P2-47 / P2-43②）。

这个函数是「旁路能力」：任何失败只记日志、不抛异常。正因为如此，它出错时**没有任何
人会被打扰**，所以这里钉死的是两件只在生产才暴露、且不会有人来报的事：

1. **推送令牌不得进日志**（P2-47）：百度要求 `token` 在查询串里，而 httpx 的异常串
   默认带完整请求 URL —— 原实现 `logger.warning(f"...{e}")` 等于把令牌写进
   `logs/app.log`（宿主挂载目录）。拿到令牌即可冒用本站配额提交任意 URL 列表。
2. **整批推送必须有总时限**（P2-43②）：原先按 `len(urls)/20` 串行 POST、每批各 10s，
   批量导入当天能跑几十分钟；APScheduler 默认 `max_instances=1`，超时会让次日调度
   直接 `MaxInstancesReachedError`（当天一条都不推）。
"""

import asyncio
import contextlib
import unittest
from unittest import mock

import httpx
from loguru import logger

from app.config import settings
from app.services import baidu_push_service as bp

_TOKEN = "SECRET-BAIDU-TOKEN-9c1f"
_URLS = [f"https://ai-cat.example.com/tutorials/{i}" for i in range(3)]
_RealAsyncClient = httpx.AsyncClient  # 打补丁前先抓住真类，否则工厂会递归调到自己


def _capture_logs(level: str = "DEBUG") -> tuple[list[str], int]:
    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m)), level=level)
    return records, sink_id


@contextlib.contextmanager
def _stub_http(handler, token: str = _TOKEN, deadline: float | None = None):
    """把推送用的 AsyncClient 换成 MockTransport —— 全程零真实网络。"""

    def _factory(**kw):
        kw.setdefault("transport", httpx.MockTransport(handler))
        return _RealAsyncClient(**kw)

    patches = [
        mock.patch.object(settings, "BAIDU_PUSH_TOKEN", token),
        mock.patch.object(settings, "SITE_BASE_URL", "https://ai-cat.example.com"),
        mock.patch.object(httpx, "AsyncClient", _factory),
    ]
    if deadline is not None:
        patches.append(mock.patch.object(bp, "_TOTAL_DEADLINE_SECONDS", deadline))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


class TlsAndScrubTests(unittest.IsolatedAsyncioTestCase):
    async def test_push_uses_https_endpoint(self):
        """明文 HTTP 下令牌就是链路明文 —— 同 host 提供 TLS，没有理由回退到 http"""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"success": len(_URLS), "remain": 98})

        with _stub_http(handler):
            result = await bp.push_urls_to_baidu(_URLS)

        self.assertEqual(len(seen), 1)
        self.assertTrue(
            str(seen[0].url).startswith("https://data.zz.baidu.com/urls"),
            f"推送端点必须是 HTTPS，实际为 {seen[0].url.scheme}://",
        )
        # 令牌仍必须在查询串里（百度 API 的硬要求）：这里断言的是「没被漏传」，
        # 防泄漏靠下面的日志净化 + 上面的 TLS
        self.assertEqual(seen[0].url.params["token"], _TOKEN)
        self.assertEqual(seen[0].url.params["site"], "ai-cat.example.com")
        self.assertEqual(result["success"], 3)
        self.assertEqual(result["remain"], 98)

    async def test_exception_message_contains_token_but_logs_do_not(self):
        """httpx 错误串带完整 URL：必须只记异常类型，否则令牌落进 logs/app.log"""
        raw_messages: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            # 复刻 httpx 的真实行为：错误消息里带上完整请求 URL
            exc = httpx.ConnectError(f"all attempts failed: {request.url}")
            raw_messages.append(str(exc))
            raise exc

        records, sink_id = _capture_logs()
        try:
            with _stub_http(handler):
                result = await bp.push_urls_to_baidu(_URLS)
        finally:
            logger.remove(sink_id)

        # 前提自证：如果异常串不含令牌，这条守卫就是恒绿的
        self.assertTrue(raw_messages and _TOKEN in raw_messages[0],
                        "桩没复现「错误串含令牌」，本用例无法证明净化有效")
        self.assertNotIn(_TOKEN, "".join(records))
        # 裁掉令牌但不能把排查价值一起裁掉
        self.assertIn("ConnectError", "".join(records))
        self.assertEqual(result["success"], 0)

    async def test_missing_token_skips_without_request(self):
        called = []

        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            called.append(request)
            return httpx.Response(200, json={})

        with _stub_http(handler, token=""):
            result = await bp.push_urls_to_baidu(_URLS)

        self.assertEqual(called, [])
        self.assertEqual(result["skipped"], len(_URLS))


class TotalDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_batches_are_cut_off_and_task_returns(self):
        """批次多且每批慢时：到点即停、正常返回，不把调度器占住到次日"""
        batches_done = []

        async def handler(request: httpx.Request) -> httpx.Response:
            batches_done.append(1)
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={"success": 20, "remain": 0})

        urls = [f"https://ai-cat.example.com/t/{i}" for i in range(400)]  # 20 批 × 50ms
        records, sink_id = _capture_logs()
        started = asyncio.get_event_loop().time()
        try:
            with _stub_http(handler, deadline=0.12):
                result = await bp.push_urls_to_baidu(urls)
        finally:
            logger.remove(sink_id)
        elapsed = asyncio.get_event_loop().time() - started

        self.assertLess(elapsed, 1.0, f"总时限没生效，跑了 {elapsed:.2f}s")
        self.assertLess(len(batches_done), 20, "本应中途截断，却把 20 批全推完了")
        self.assertIn("放弃剩余", "".join(records))
        self.assertNotIn(_TOKEN, "".join(records))
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
