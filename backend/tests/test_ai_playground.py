"""AI 对话调试台服务层测试：小时限流、消息校验与流式事件。

钉三条不变量：

1. **小时限流独立于 AI 写作**（键隔离，各算各的），Redis 故障放行 —— 限流器
   故障不该把调试动作打死，成本护栏仍兜底；
2. **模型严格解析不回退**：显式选择的模型不可用必须 503，绝不悄悄换模型
   （换模型=换钱也换质量，配置者看不见）；
3. **用量记账在 finally**：上游抛错也要记账（已发生的 token 不能白烧），
   且 model 键要在 provider clear() 之后恢复。
"""

import unittest
from unittest import mock

from app.core.exceptions import AppException
from app.schemas.ai_playground import PlaygroundRunRequest, PlaygroundTurn
from app.services.ai_playground import service as playground


class _FakeProvider:
    """可编程的假 Provider：yield 指定增量，按需抛错，记录 usage_out 写入"""

    def __init__(self, chunks=("你", "好"), *, error_on_first=False, model_key="fake-model"):
        self.chunks = tuple(chunks)
        self.error_on_first = error_on_first
        self.model_key = model_key
        self.calls: list[dict] = []

    async def stream_chat(self, messages, usage_out=None, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        if usage_out is not None:
            usage_out.update({"prompt_tokens": 5, "completion_tokens": 7})
        if self.error_on_first:
            raise AppException(code=503, message="上游炸了")
        for c in self.chunks:
            # 模拟 openai 兼容层的 clear() 语义：会擦掉调用方写入的 model 键
            if usage_out is not None:
                usage_out.pop("model", None)
            yield c


def _req(**kwargs) -> PlaygroundRunRequest:
    base = dict(messages=[PlaygroundTurn(role="user", content="你好")])
    base.update(kwargs)
    return PlaygroundRunRequest(**base)


class HourlyLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.conftest import FakeRedis

        self.redis = FakeRedis()
        self._orig = playground.redis_client
        playground.redis_client = self.redis

    async def asyncTearDown(self):
        playground.redis_client = self._orig

    async def test_limit_is_per_admin_and_independent_of_writing(self):
        from unittest.mock import patch

        with patch.object(playground, "HOURLY_LIMIT", 3):
            for _ in range(3):
                await playground.check_hourly_limit(7)
            with self.assertRaises(AppException) as ctx:
                await playground.check_hourly_limit(7)
            self.assertEqual(ctx.exception.code, 429)
            # 别的管理员不受影响
            await playground.check_hourly_limit(8)
            # 与 AI 写作的键隔离：写作计数不占调试额度
            keys = list(self.redis._store.keys())
            self.assertTrue(all(k.startswith("ai_playground:rate:") for k in keys))

    async def test_redis_down_passes(self):
        class _Broken:
            async def incr(self, *a, **k):
                raise ConnectionError("down")

            async def expire(self, *a, **k):
                raise ConnectionError("down")

        playground.redis_client = _Broken()
        # 限流器故障放行（成本护栏兜底），不抛异常
        await playground.check_hourly_limit(7)


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_deltas_and_records_usage(self):
        provider = _FakeProvider()
        with mock.patch.object(playground.factory, "get_provider", mock.AsyncMock(return_value=provider)), \
             mock.patch.object(playground.ai_usage_service, "record_provider_usage", mock.AsyncMock()) as rec:
            events = []
            async for event in playground.run(_req(), usage_out=(u := {}), admin_id=1):
                events.append(event)
        self.assertEqual([e for e, _ in events], ["delta", "delta"])
        self.assertEqual("".join(d["text"] for _, d in events), "你好")
        # system prompt 只在配置时出现；消息原样透传
        self.assertEqual(provider.calls[0]["messages"][0]["role"], "user")
        self.assertEqual(u["completion_tokens"], 7)
        self.assertEqual(u["model"], "fake-model", "provider clear() 后 model 键必须恢复")
        rec.assert_awaited_once()

    async def test_system_prompt_prepended(self):
        provider = _FakeProvider()
        with mock.patch.object(playground.factory, "get_provider", mock.AsyncMock(return_value=provider)), \
             mock.patch.object(playground.ai_usage_service, "record_provider_usage", mock.AsyncMock()):
            async for _ in playground.run(
                _req(system_prompt="你是测试助手"), usage_out={}, admin_id=1,
            ):
                pass
        self.assertEqual(provider.calls[0]["messages"][0]["role"], "system")
        self.assertEqual(provider.calls[0]["messages"][1]["role"], "user")

    async def test_usage_recorded_even_when_provider_fails(self):
        provider = _FakeProvider(error_on_first=True)
        with mock.patch.object(playground.factory, "get_provider", mock.AsyncMock(return_value=provider)), \
             mock.patch.object(playground.ai_usage_service, "record_provider_usage", mock.AsyncMock()) as rec:
            with self.assertRaises(AppException):
                async for _ in playground.run(_req(), usage_out={}, admin_id=1):
                    pass
        rec.assert_awaited_once(), "上游炸掉也要记账：已发生的 token 不能白烧"

    async def test_unknown_model_never_falls_back(self):
        from app.services import ai_model_service

        with mock.patch.object(
            playground.ai_model_service, "load_enabled_model", mock.AsyncMock(return_value=None)
        ):
            with self.assertRaises(AppException) as ctx:
                async for _ in playground.run(
                    _req(model_code="gone"), usage_out={}, admin_id=1,
                ):
                    pass
        self.assertEqual(ctx.exception.code, 503)

    async def test_validation_rejects_bad_shapes(self):
        # 最后一条必须是用户消息
        with self.assertRaises(AppException):
            async for _ in playground.run(
                _req(messages=[PlaygroundTurn(role="user", content="a"),
                               PlaygroundTurn(role="assistant", content="b")]),
                usage_out={}, admin_id=1,
            ):
                pass
        # 空内容消息（绕过 pydantic 的直接构造防御）
        with mock.patch.object(playground, "MAX_MESSAGE_CHARS", 0):
            with self.assertRaises(AppException):
                async for _ in playground.run(_req(), usage_out={}, admin_id=1):
                    pass


if __name__ == "__main__":
    unittest.main()
