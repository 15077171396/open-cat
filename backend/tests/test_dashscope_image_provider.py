"""通义万相图像 Provider 契约 + 图像模型接入工厂。

万相是异步任务协议（提交拿 task_id → 轮询结果），且返回的是 24 小时临时 URL。
本文件钉住：提交/轮询的请求形态、失败任务必须报错而不是返回空图、未配置 Key
不得发出请求，以及图像模型只成为图像默认且只能被图像工厂取到。
"""

import json
import unittest
from unittest import mock

import httpx

from app.core.exceptions import AppException
from app.services.ai import factory
from app.services.ai.dashscope_image_provider import DashScopeImageProvider


def _provider(handler):
    provider = DashScopeImageProvider.__new__(DashScopeImageProvider)
    provider.api_key = "sk-test"
    provider.base_url = "https://dashscope.aliyuncs.com"
    provider.default_model = "wanx2.1-t2i-turbo"
    provider.client = httpx.AsyncClient(
        base_url=provider.base_url,
        headers={"Authorization": "Bearer sk-test"},
        transport=httpx.MockTransport(handler),
    )
    return provider


class DashScopeImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_poll_and_return_urls(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.url.path.endswith("/image-synthesis"):
                body = json.loads(request.content)
                assert body["parameters"]["size"] == "1280*720"
                return httpx.Response(200, json={"output": {"task_id": "t1"}})
            if request.url.path == "/api/v1/tasks/t1":
                return httpx.Response(200, json={"output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "https://dashscope-result.aliyuncs.com/a.png"}],
                }})
            return httpx.Response(404)

        provider = _provider(handler)
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            urls = await provider.generate("一只猫", size="1280*720", n=1)
        await provider.close()
        self.assertEqual(urls, ["https://dashscope-result.aliyuncs.com/a.png"])
        self.assertEqual(calls[0], ("POST", "/api/v1/services/aigc/text2image/image-synthesis"))
        self.assertIn(("GET", "/api/v1/tasks/t1"), calls)

    async def test_failed_task_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/image-synthesis"):
                return httpx.Response(200, json={"output": {"task_id": "t2"}})
            return httpx.Response(200, json={"output": {"task_status": "FAILED", "message": "bad"}})

        provider = _provider(handler)
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            with self.assertRaises(AppException):
                await provider.generate("x", size="1024*1024", n=1)
        await provider.close()

    async def test_missing_key_raises(self):
        provider = _provider(lambda request: httpx.Response(500))
        provider.api_key = ""
        with self.assertRaises(AppException):
            await provider.generate("x", size="1024*1024", n=1)
        await provider.close()

    async def test_compatible_mode_base_url_is_normalized(self):
        """复用 Qwen 配置时 base_url 常是兼容模式地址；万相原生任务 API 在根域下。"""
        from app.services.ai_config_service import ProviderConfig

        provider = DashScopeImageProvider(ProviderConfig(
            api_key="sk-test",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            default_model="wanx2.1-t2i-turbo",
        ))
        try:
            self.assertEqual(provider.base_url, "https://dashscope.aliyuncs.com")
            self.assertEqual(
                str(provider.client.base_url), "https://dashscope.aliyuncs.com"
            )
        finally:
            await provider.close()


class ImageModelWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.services import ai_model_service
        from tests.conftest import make_user
        from tests.fake_db import MemorySession

        self.db = MemorySession()
        self.admin = make_user(role="admin")
        self._patch = mock.patch.object(
            ai_model_service, "_invalidate_runtime_caches", new=mock.AsyncMock()
        )
        self._patch.start()
        factory._providers.clear()

    async def asyncTearDown(self):
        self._patch.stop()
        # 关闭工厂缓存的实例再清空：测试里也不留未关闭的 httpx 连接池
        await factory.close_all_providers()

    async def test_image_model_becomes_image_default_only(self):
        from app.models.ai_model import AiModel
        from app.services import ai_model_service

        await ai_model_service.create_model(self.db, {
            "display_name": "DeepSeek", "provider": "deepseek", "is_enabled": True,
        }, self.admin)
        await ai_model_service.create_model(self.db, {
            "display_name": "万相", "provider": "dashscope_image",
            "modality": "image", "api_key": "sk-test", "image_price": 0.2,
            "is_enabled": True,
        }, self.admin)
        rows = self.db.all_rows(AiModel)
        self.assertEqual(len([r for r in rows if r["modality"] == "text" and r["is_default"]]), 1)
        self.assertEqual(len([r for r in rows if r["modality"] == "image" and r["is_default"]]), 1)

        from app.services.ai_config_service import ProviderConfig
        cfg = ProviderConfig(
            api_key="sk-test", base_url="https://dashscope.aliyuncs.com",
            default_model="wanx2.1-t2i-turbo", provider="dashscope_image",
            code="wanx", display_name="万相", modality="image", image_price=0.2,
        )
        with mock.patch.object(factory, "get_runtime_config", new=mock.AsyncMock(return_value=cfg)):
            self.assertIsInstance(await factory.get_image_provider(None), DashScopeImageProvider)

        with self.assertRaises(AppException):
            await ai_model_service.create_model(self.db, {
                "display_name": "错配", "provider": "dashscope_image",
                "modality": "text", "is_enabled": True,
            }, self.admin)

    def test_text_factory_rejects_image_provider(self):
        """图像 Provider 不能当文本模型用：工厂按模态取不到它。"""
        self.assertIsNone(factory.provider_factory("dashscope_image", modality="text"))
        self.assertIs(
            factory.provider_factory("dashscope_image", modality="image"),
            DashScopeImageProvider,
        )
