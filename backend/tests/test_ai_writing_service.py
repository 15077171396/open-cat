import unittest
from types import SimpleNamespace
from unittest import mock

from app.core.exceptions import AppException
from app.schemas.ai_writing import (
    ContextPayload, ImageRequest, SelectionPayload, WritingRunRequest,
)
from app.services.ai_writing import service
from app.services.ai_writing.prompts import PromptDefault


class FakeProvider:
    model_key = "deepseek"
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def chat(self, messages, usage_out=None, **kwargs):
        self.calls += 1
        if usage_out is not None:
            usage_out.clear()
            usage_out.update({"prompt_tokens": 10, "completion_tokens": 5})
        return self.replies.pop(0)

    async def stream_chat(self, messages, usage_out=None, **kwargs):
        yield "改写"
        yield "结果"
        if usage_out is not None:
            usage_out.update({"prompt_tokens": 8, "completion_tokens": 2})


class EmptyProvider(FakeProvider):
    def __init__(self):
        super().__init__([])

    async def stream_chat(self, messages, usage_out=None, **kwargs):
        if False:  # pragma: no cover 让函数保持异步生成器语义
            yield ""


class PinnedProvider(FakeProvider):
    model_key = "pinned-model"


def _request(operation="rewrite", **kwargs):
    return WritingRunRequest(
        operation=operation, content_type="node",
        selection=SelectionPayload(index=0, length=2, text="原文"),
        context=ContextPayload(title="标题"), **kwargs,
    )


def _prompt_row(model_code=""):
    return SimpleNamespace(
        operation="rewrite", display_name="改写", description="",
        system_prompt="sys", user_template="{{selection}}", output_type="markdown",
        model_code=model_code, temperature=0.7, max_tokens=100, max_input_chars=4000,
        sort_order=1,
    )


class WritingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_text_events(self):
        prompt = PromptDefault("rewrite", "改写", "", "sys", "{{selection}}", "markdown",
                               "", 0.7, 100, 4000, 1)
        provider = FakeProvider([])
        usage = {}
        events = [e async for e in service.iter_text_events(_request(), prompt, provider, usage)]
        self.assertEqual([e[0] for e in events], ["delta", "delta"])
        self.assertEqual(usage["completion_tokens"], 2)

    async def test_json_repair_once(self):
        prompt = PromptDefault("titles", "标题候选", "", "sys", "{{article}}", "json",
                               "", 0.7, 100, 4000, 1)
        provider = FakeProvider(["不是 JSON", '{"titles": ["A 标题"]}'])
        events = [e async for e in service.iter_text_events(_request("titles"), prompt, provider, {})]
        self.assertEqual(events[-1][0], "result")
        self.assertEqual(events[-1][1]["titles"], ["A 标题"])
        self.assertEqual(provider.calls, 2)

    async def test_json_twice_invalid_raises(self):
        prompt = PromptDefault("titles", "标题候选", "", "sys", "{{article}}", "json",
                               "", 0.7, 100, 4000, 1)
        provider = FakeProvider(["bad", "still bad"])
        with self.assertRaises(AppException):
            [e async for e in service.iter_text_events(_request("titles"), prompt, provider, {})]

    async def test_illustrate_requires_prompt(self):
        with self.assertRaises(AppException):
            await service.validate_image_request(ImageRequest(prompt=""))

    async def test_run_restores_model_after_provider_clears_usage(self):
        """回归：provider 写 usage_out 时会 clear()，run 必须恢复 model 供 API 层计价。"""
        prompt_row = SimpleNamespace(
            operation="titles", display_name="标题候选", description="",
            system_prompt="sys", user_template="{{article}}", output_type="json",
            model_code="", temperature=0.7, max_tokens=100, max_input_chars=4000,
            sort_order=1,
        )
        provider = FakeProvider(['{"titles": ["A 标题"]}'])
        usage: dict = {}
        with (
            mock.patch.object(
                service, "get_prompt_by_operation", mock.AsyncMock(return_value=prompt_row)
            ),
            mock.patch.object(
                service.factory, "get_provider", mock.AsyncMock(return_value=provider)
            ),
            mock.patch.object(
                service.ai_usage_service, "record_provider_usage", mock.AsyncMock()
            ) as record,
        ):
            events = [event async for event in service.run(_request("titles"), usage_out=usage)]

        self.assertEqual(events[-1][0], "result")
        self.assertEqual(usage["model"], "deepseek")
        self.assertEqual(usage["prompt_tokens"], 10)
        self.assertEqual(usage["completion_tokens"], 5)
        record.assert_awaited_once()
        self.assertEqual(record.await_args.kwargs["usage"]["model"], "deepseek")

    def test_temperature_zero_is_honored(self):
        """0 是显式配置（确定性输出），不能被 `or 0.7` 吞掉。"""
        self.assertEqual(service._prompt_default(_prompt_row()).temperature, 0.7)
        zero = _prompt_row()
        zero.temperature = 0
        self.assertEqual(service._prompt_default(zero).temperature, 0.0)
        missing = _prompt_row()
        missing.temperature = None
        self.assertEqual(service._prompt_default(missing).temperature, 0.7)

    async def test_pinned_model_missing_or_disabled_is_503_without_fallback(self):
        with (
            mock.patch.object(
                service, "get_prompt_by_operation",
                mock.AsyncMock(return_value=_prompt_row("pinned-model")),
            ),
            mock.patch.object(
                service.ai_model_service, "load_enabled_model",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                service.factory, "get_provider", mock.AsyncMock()
            ) as get_provider,
        ):
            with self.assertRaises(AppException) as ctx:
                [event async for event in service.run(_request(), usage_out={})]
        self.assertEqual(ctx.exception.code, 503)
        self.assertIn("提示词绑定的模型不可用", ctx.exception.message)
        get_provider.assert_not_awaited()

    async def test_pinned_model_resolves_only_the_same_code(self):
        prompt_row = _prompt_row("pinned-model")
        provider = PinnedProvider([])
        with (
            mock.patch.object(
                service, "get_prompt_by_operation", mock.AsyncMock(return_value=prompt_row)
            ),
            mock.patch.object(
                service.ai_model_service, "load_enabled_model",
                mock.AsyncMock(return_value=SimpleNamespace(code="pinned-model")),
            ),
            mock.patch.object(
                service.factory, "get_provider", mock.AsyncMock(return_value=provider)
            ) as get_provider,
            mock.patch.object(
                service.ai_usage_service, "record_provider_usage", mock.AsyncMock()
            ),
        ):
            events = [event async for event in service.run(_request(), usage_out={})]
        self.assertEqual(events[0][0], "delta")
        get_provider.assert_awaited_once_with("pinned-model")

    async def test_pinned_model_factory_fallback_is_rejected(self):
        """工厂若因竞态回退到别的模型，宁可 503 也不能拿错模型的结果充数。"""
        prompt_row = _prompt_row("pinned-model")
        with (
            mock.patch.object(
                service, "get_prompt_by_operation", mock.AsyncMock(return_value=prompt_row)
            ),
            mock.patch.object(
                service.ai_model_service, "load_enabled_model",
                mock.AsyncMock(return_value=SimpleNamespace(code="pinned-model")),
            ),
            mock.patch.object(
                service.factory, "get_provider",
                mock.AsyncMock(return_value=FakeProvider([])),
            ),
        ):
            with self.assertRaises(AppException) as ctx:
                [event async for event in service.run(_request(), usage_out={})]
        self.assertEqual(ctx.exception.code, 503)

    async def test_request_model_overrides_prompt_binding(self):
        """面板临时选模型优先于提示词绑定，并严格按该 code 解析。"""
        prompt_row = _prompt_row("pinned-model")
        provider = FakeProvider([])
        provider.model_key = "qwen"
        load = mock.AsyncMock(return_value=SimpleNamespace(code="qwen"))
        with (
            mock.patch.object(
                service, "get_prompt_by_operation", mock.AsyncMock(return_value=prompt_row)
            ),
            mock.patch.object(service.ai_model_service, "load_enabled_model", load),
            mock.patch.object(
                service.factory, "get_provider", mock.AsyncMock(return_value=provider)
            ) as get_provider,
            mock.patch.object(
                service.ai_usage_service, "record_provider_usage", mock.AsyncMock()
            ),
        ):
            events = [event async for event in service.run(
                _request(model_code="qwen"), usage_out={}
            )]
        self.assertEqual(events[0][0], "delta")
        load.assert_awaited_once_with("qwen", modality="text")
        get_provider.assert_awaited_once_with("qwen")

    async def test_request_model_disabled_is_503_without_fallback(self):
        with (
            mock.patch.object(
                service, "get_prompt_by_operation", mock.AsyncMock(return_value=_prompt_row())
            ),
            mock.patch.object(
                service.ai_model_service, "load_enabled_model",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(service.factory, "get_provider", mock.AsyncMock()) as get_provider,
        ):
            with self.assertRaises(AppException) as ctx:
                [event async for event in service.run(
                    _request(model_code="gone-model"), usage_out={}
                )]
        self.assertEqual(ctx.exception.code, 503)
        self.assertIn("选择的模型不可用", ctx.exception.message)
        get_provider.assert_not_awaited()

    async def test_request_image_model_is_strict(self):
        provider = SimpleNamespace(model_key="wanx")
        with (
            mock.patch.object(
                service.ai_model_service, "load_enabled_model",
                mock.AsyncMock(return_value=SimpleNamespace(code="wanx")),
            ),
            mock.patch.object(
                service.factory, "get_image_provider",
                mock.AsyncMock(return_value=provider),
            ) as get_provider,
        ):
            self.assertIs(await service._resolve_image_provider("wanx"), provider)
        get_provider.assert_awaited_once_with("wanx")

    async def test_request_image_model_disabled_is_503(self):
        with (
            mock.patch.object(
                service.ai_model_service, "load_enabled_model",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                service.factory, "get_image_provider", mock.AsyncMock()
            ) as get_provider,
        ):
            with self.assertRaises(AppException) as ctx:
                await service._resolve_image_provider("gone-image")
        self.assertEqual(ctx.exception.code, 503)
        self.assertIn("插图模型", ctx.exception.message)
        get_provider.assert_not_awaited()

    async def test_empty_stream_is_an_error_not_empty_success(self):
        with (
            mock.patch.object(
                service, "get_prompt_by_operation", mock.AsyncMock(return_value=_prompt_row())
            ),
            mock.patch.object(
                service.factory, "get_provider", mock.AsyncMock(return_value=EmptyProvider())
            ),
            mock.patch.object(
                service.ai_usage_service, "record_provider_usage", mock.AsyncMock()
            ),
        ):
            with self.assertRaises(AppException) as ctx:
                [event async for event in service.run(_request(), usage_out={})]
        self.assertEqual(ctx.exception.code, 503)
        self.assertIn("AI 返回空内容", ctx.exception.message)
