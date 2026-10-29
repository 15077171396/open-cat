"""自定义图像（OpenAI 兼容）Provider 契约测试。

覆盖两类常见返回：`data[].url` 与只给 `b64_json` 的网关。
非 OpenAI 协议仍需独立 Provider —— 这里只保证“协议相同”的服务能靠配置接入。
"""

import base64
import json
import unittest

import httpx

from app.core.exceptions import AppException
from app.services.ai.custom_image_provider import CustomImageProvider
from app.services.ai_config_service import ProviderConfig

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _config(**overrides):
    data = {
        "api_key": "sk-test",
        "base_url": "https://api.example.com/v1",
        "default_model": "image-model",
        "provider": "custom_image",
        "code": "my-image",
        "display_name": "自定义图像",
        "modality": "image",
    }
    data.update(overrides)
    return ProviderConfig(**data)


def _provider(handler, config=None):
    provider = CustomImageProvider(config or _config())
    provider.client = httpx.AsyncClient(
        base_url=provider.base_url,
        headers={"Authorization": f"Bearer {provider.api_key}"},
        transport=httpx.MockTransport(handler),
    )
    return provider


class CustomImageProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_url_response_and_payload(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"data": [
                {"url": "https://cdn.example.com/a.png"},
            ]})

        provider = _provider(handler)
        urls = await provider.generate("一只猫", size="1280*720", n=2)
        await provider.close()

        self.assertEqual(urls, ["https://cdn.example.com/a.png"])
        self.assertEqual(calls[0].url.path, "/v1/images/generations")
        body = json.loads(calls[0].content)
        self.assertEqual(body["size"], "1280x720")
        self.assertEqual(body["n"], 2)
        self.assertEqual(body["model"], "image-model")

    async def test_b64_response_becomes_data_url(self):
        encoded = base64.b64encode(PNG).decode()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

        provider = _provider(handler)
        urls = await provider.generate("一只猫")
        await provider.close()

        self.assertTrue(urls[0].startswith("data:image/png;base64,"))

    async def test_missing_config_is_503(self):
        provider = _provider(
            lambda request: httpx.Response(500),
            _config(api_key="", default_model=""),
        )
        with self.assertRaises(AppException) as ctx:
            await provider.generate("x")
        await provider.close()
        self.assertEqual(ctx.exception.code, 503)
        self.assertIn("未配置完整", ctx.exception.message)

    async def test_unexpected_payload_is_503(self):
        provider = _provider(lambda request: httpx.Response(200, json={"data": []}))
        with self.assertRaises(AppException) as ctx:
            await provider.generate("x")
        await provider.close()
        self.assertEqual(ctx.exception.code, 503)
