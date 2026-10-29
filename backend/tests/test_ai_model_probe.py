"""自定义供应商 + 模型「测试连接」守卫。

两件事都在解决同一类问题：**配置错了没人知道**。
- 只有三家内置供应商时，想接 Moonshot / 智谱 / Ollama 只能改代码；
  现在 `custom`（OpenAI 兼容）一个入口全覆盖，但必须强制填 Base URL 与上游模型名 ——
  否则错到调用期，报的错和「漏填一项」毫无关系。
- Key / Base URL / 模型名填错的表现是「用户在工具页看到 AI 服务不可用」，
  管理员在后台看不出来。测试连接把它变成一次点击，且**失败也返回结构化原因**。

本文件钉：供应商注册与校验、探测结果的四种形态（成功/未配置/上游报错/超时）、
冷却、路由权限码、审计动作词表。
"""

import unittest
from unittest import mock

import httpx

from app.core.exceptions import AppException
from app.models.admin_audit_log import AUDIT_ACTIONS, AUDIT_ACTION_RISK
from app.models.ai_model import AiModel
from app.services import ai_model_probe_service, ai_model_service
from app.services.ai import registry
from app.services.ai.custom_provider import CustomProvider
from tests.fake_db import MemorySession


def _model(**kwargs) -> AiModel:
    data = {
        "code": "moonshot-8k",
        "display_name": "Moonshot 8K",
        "provider": "custom",
        "model_name": "moonshot-v1-8k",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_enc": "",
        "input_price": 0,
        "output_price": 0,
        "is_enabled": True,
        "is_default": False,
        "sort_order": 0,
        "remark": None,
    }
    data.update(kwargs)
    return AiModel(**data)


class CustomProviderTests(unittest.TestCase):
    """① 自定义供应商在注册表与下拉里都存在"""

    def test_registry_contains_custom(self):
        self.assertIn("custom", registry.registered_provider_names())

    def test_provider_option_is_offered_to_admin(self):
        payload = {p["name"]: p for p in ai_model_service.providers_payload()}
        self.assertIn("custom", payload)
        self.assertIn("自定义", payload["custom"]["display_name"])

    def test_provider_is_openai_compatible(self):
        """协议复用而不是另写一套：换协议才需要新类"""
        from app.services.ai.openai_compat import OpenAICompatProvider

        self.assertTrue(issubclass(CustomProvider, OpenAICompatProvider))

    def test_requires_base_url_and_model_name(self):
        for base_url, model_name, missing in (
            ("", "moonshot-v1-8k", "Base URL"),
            ("https://api.moonshot.cn/v1", "", "上游模型名"),
            ("", "", "Base URL"),
        ):
            with self.subTest(base_url=base_url, model_name=model_name):
                with self.assertRaises(AppException) as ctx:
                    ai_model_service._require_custom_endpoint("custom", base_url, model_name)
                self.assertIn(missing, ctx.exception.message)

    def test_builtin_providers_need_no_endpoint(self):
        """内置三家有 .env 默认值，不能因为它们空着就拒建模型"""
        ai_model_service._require_custom_endpoint("deepseek", "", "")

    def test_accepts_complete_custom_endpoint(self):
        ai_model_service._require_custom_endpoint("custom", "https://api.moonshot.cn/v1", "moonshot-v1-8k")


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    """② 探测的四种结果形态"""

    async def asyncSetUp(self):
        ai_model_probe_service.reset_cooldown()
        self.db = MemorySession()
        row = _model()
        self.db.add(row)
        await self.db.commit()
        self.model_id = row.id

    async def _probe(self, provider):
        with mock.patch.object(ai_model_probe_service, "get_provider", return_value=provider):
            return await ai_model_probe_service.probe_model(self.db, self.model_id)

    async def test_success_returns_latency_and_reply(self):
        class _P:
            async def chat(self, messages, **kwargs):
                kwargs["usage_out"].update({"prompt_tokens": 12, "completion_tokens": 2})
                return "pong"

        result = await self._probe(_P())
        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "pong")
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.assertEqual(result["upstream_model"], "moonshot-v1-8k")
        self.assertEqual(result["usage"]["prompt_tokens"], 12)

    async def test_unconfigured_key_is_reported_not_raised(self):
        class _P:
            async def chat(self, messages, **kwargs):
                raise AppException(code=503, message="自定义（OpenAI 兼容）未配置，请联系管理员")

        result = await self._probe(_P())
        self.assertFalse(result["ok"])
        self.assertIn("未配置", result["error"])

    async def test_upstream_http_error_keeps_body(self):
        request = httpx.Request("POST", "https://api.moonshot.cn/v1/chat/completions")
        response = httpx.Response(401, text='{"error":"invalid api key"}', request=request)

        class _P:
            async def chat(self, messages, **kwargs):
                raise httpx.HTTPStatusError("401", request=request, response=response)

        result = await self._probe(_P())
        self.assertFalse(result["ok"])
        self.assertIn("401", result["error"])
        self.assertIn("invalid api key", result["error"])

    async def test_timeout_has_actionable_message(self):
        class _P:
            async def chat(self, messages, **kwargs):
                raise httpx.ReadTimeout("timed out")

        result = await self._probe(_P())
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["error"])

    async def test_unknown_model_is_404(self):
        with self.assertRaises(AppException) as ctx:
            await ai_model_probe_service.probe_model(self.db, 999_999)
        self.assertEqual(ctx.exception.code, 404)

    async def test_image_model_is_rejected_without_provider_call(self):
        """图像模型没有 chat 接口：探测不能回退文本默认模型后谎报成功。

        回归守卫：`get_provider(code)` 默认走文本模态，图像行的 code 在文本目录里
        查不到会回退全局文本默认模型 —— 探活结果「成功」，回显的却是图像行的身份。
        """
        image_row = _model(
            code="wanx-image",
            display_name="万相图像",
            provider="dashscope_image",
            model_name="wanx-v1",
            modality="image",
        )
        self.db.add(image_row)
        await self.db.commit()

        provider = mock.AsyncMock()
        provider.return_value.chat = mock.AsyncMock(return_value="pong")
        with mock.patch.object(ai_model_probe_service, "get_provider", provider):
            result = await ai_model_probe_service.probe_model(self.db, image_row.id)

        provider.assert_not_awaited()
        self.assertFalse(result["ok"])
        self.assertIn("图像模型暂不支持测试连接", result["error"])
        self.assertEqual(result["model"], "wanx-image")

    async def test_cooldown_blocks_repeat(self):
        class _P:
            async def chat(self, messages, **kwargs):
                return "pong"

        await self._probe(_P())
        with self.assertRaises(AppException) as ctx:
            await self._probe(_P())
        self.assertEqual(ctx.exception.code, 429)


class ProbeWiringTests(unittest.TestCase):
    """③ 路由、权限码、审计词表"""

    def test_route_registered(self):
        from app.main import app

        self.assertIn("/api/v1/admin/ai-models/{model_id}/test", app.openapi()["paths"])

    def test_permission_is_edit(self):
        from app.core.rbac import match_route_permission

        self.assertEqual(
            match_route_permission("POST", "/api/v1/admin/ai-models/7/test"), ["ai_model:edit"]
        )

    def test_audit_action_has_risk_level(self):
        self.assertIn("ai_model_test", AUDIT_ACTIONS)
        self.assertIn("ai_model_test", AUDIT_ACTION_RISK)

    def test_probe_does_not_touch_quota(self):
        """探测不得记账：只调用 provider.chat，不引 ai_usage_service"""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "app" / "services" / "ai_model_probe_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("record_tokens", source)
        self.assertNotIn("reserve_quota", source)
