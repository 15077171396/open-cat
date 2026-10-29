import unittest

import httpx

from app.core.exceptions import AppException
from app.services.ai.deepseek_provider import DeepSeekProvider


def _provider(handler):
    provider = DeepSeekProvider.__new__(DeepSeekProvider)
    provider.api_key = "sk-test"
    provider.base_url = "https://api.deepseek.com/v1"
    provider.default_model = "deepseek-chat"
    provider.client = httpx.AsyncClient(
        base_url=provider.base_url,
        headers={"Authorization": "Bearer sk-test"},
        transport=httpx.MockTransport(handler),
    )
    return provider


SSE_OK = (
    b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n\n'
    b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":12,"completion_tokens":2}}\n\n'
    b'data: [DONE]\n\n'
)


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_content_and_usage(self):
        provider = _provider(lambda request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=SSE_OK))
        usage = {}
        chunks = [c async for c in provider.stream_chat(
            [{"role": "user", "content": "hi"}], usage_out=usage)]
        await provider.close()
        self.assertEqual("".join(chunks), "你好")
        self.assertEqual(usage["completion_tokens"], 2)

    async def test_finish_reason_survives_usage_frame(self):
        """finish 帧在 usage 帧之前：usage_out.clear() 不能把结束原因一起擦掉。"""
        body = (
            b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n'
            b'data: [DONE]\n\n'
        )
        provider = _provider(lambda request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=body))
        usage = {}
        chunks = [c async for c in provider.stream_chat(
            [{"role": "user", "content": "hi"}], usage_out=usage)]
        await provider.close()
        self.assertEqual("".join(chunks), "x")
        self.assertEqual(usage["finish_reason"], "length")
        self.assertEqual(usage["completion_tokens"], 4)

    async def test_error_frame_raises(self):
        body = b'data: {"error":{"message":"rate limited"}}\n\n'
        provider = _provider(lambda request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=body))
        with self.assertRaises(AppException):
            [c async for c in provider.stream_chat([{"role": "user", "content": "hi"}])]
        await provider.close()

    async def test_json_error_body_raises(self):
        # 上游限流时可能以 HTTP 200 + 纯 JSON 错误体（非 SSE）返回：没有任何 data: 帧，
        # 若不显式识别，生成器会静默结束（不产出、不报错），调用方拿不到任何信号。
        body = b'{"error":{"message":"rate limited"}}'
        provider = _provider(lambda request: httpx.Response(
            200, headers={"Content-Type": "application/json"}, content=body))
        with self.assertRaises(AppException) as ctx:
            [c async for c in provider.stream_chat([{"role": "user", "content": "hi"}])]
        await provider.close()
        self.assertEqual(ctx.exception.code, 503)
