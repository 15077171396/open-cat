import asyncio
import base64
import unittest
from unittest import mock

from app.core.exceptions import AppException
from app.schemas.ai_writing import ImageRequest
from app.services.ai_writing import image_pipeline


class FakeImageProvider:
    model_key = "wanx"
    image_price = 0.2
    def __init__(self, urls=None):
        self.urls = urls or ["https://dashscope-result.aliyuncs.com/a.png"]
        self.calls = 0

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        return list(self.urls)


class SlowImageProvider(FakeImageProvider):
    async def generate(self, prompt, **kwargs):
        self.calls += 1
        await asyncio.sleep(10)
        return list(self.urls)


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class ImagePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_events_and_cos_transfer(self):
        async def fake_download(url, **kwargs):
            return PNG, "image/png", 1024, 768

        with mock.patch.object(image_pipeline.cos_service, "is_configured", return_value=True), \
             mock.patch.object(image_pipeline, "_download_image", new=fake_download), \
             mock.patch.object(image_pipeline.cos_service, "upload_bytes",
                               new=mock.AsyncMock(return_value="https://cdn.example.com/a.png")), \
             mock.patch.object(image_pipeline.ai_usage_service, "record_image_usage",
                               new=mock.AsyncMock()) as record:
            events = [e async for e in image_pipeline.iter_images(
                ImageRequest(prompt="一只猫", alt="猫", size="1024*768", count=1),
                provider=FakeImageProvider(),
            )]
        self.assertEqual([e[0] for e in events], ["status", "status", "image"])
        self.assertEqual(events[-1][1]["url"], "https://cdn.example.com/a.png")
        record.assert_awaited_once_with("wanx", 1, 0.2)

    async def test_unconfigured_cos_blocks_generation(self):
        """COS 未配置必须在付费生成之前拒绝，且不能被逐张降级守卫吞掉。"""
        provider = FakeImageProvider()
        with mock.patch.object(image_pipeline.cos_service, "is_configured", return_value=False):
            with self.assertRaises(AppException) as ctx:
                [e async for e in image_pipeline.iter_images(
                    ImageRequest(prompt="一只猫", count=1), provider=provider,
                )]
        self.assertEqual(ctx.exception.code, 503)
        self.assertIn("COS 未配置", ctx.exception.message)
        self.assertEqual(provider.calls, 0, "COS 未配置时绝不允许调用图像生成")

    async def test_slow_generation_times_out_as_504(self):
        async def fake_download(url, **kwargs):  # pragma: no cover 生成就超时了
            return PNG, "image/png", 1024, 768

        with mock.patch.object(image_pipeline.cos_service, "is_configured", return_value=True), \
             mock.patch.object(image_pipeline, "IMAGE_TOTAL_BUDGET", 0.05), \
             mock.patch.object(image_pipeline, "_download_image", new=fake_download):
            with self.assertRaises(AppException) as ctx:
                [e async for e in image_pipeline.iter_images(
                    ImageRequest(prompt="一只猫", count=1), provider=SlowImageProvider(),
                )]
        self.assertEqual(ctx.exception.code, 504)

    async def test_records_image_before_emitting_so_disconnect_keeps_usage(self):
        """断线回归：客户端收到第一张就断开时，已转存图片的用量/费用也必须已落账。

        旧实现把记账放在循环之后，生成器在 ``yield`` 处被关闭后这段代码永远不执行 ——
        站内已有图片文件，额度与费用却是 0。
        """
        async def fake_download(url, **kwargs):
            return PNG, "image/png", 1024, 768

        provider = FakeImageProvider([
            "https://dashscope-result.aliyuncs.com/a.png",
            "https://dashscope-result.aliyuncs.com/b.png",
        ])
        usage = {}
        with mock.patch.object(image_pipeline.cos_service, "is_configured", return_value=True), \
             mock.patch.object(image_pipeline, "_download_image", new=fake_download), \
             mock.patch.object(image_pipeline.cos_service, "upload_bytes",
                               new=mock.AsyncMock(side_effect=[
                                   "https://cdn.example.com/a.png",
                                   "https://cdn.example.com/b.png",
                               ])), \
             mock.patch.object(image_pipeline.ai_usage_service, "record_image_usage",
                               new=mock.AsyncMock()) as record:
            agen = image_pipeline.iter_images(
                ImageRequest(prompt="一只猫", size="1024*768", count=2),
                provider=provider, usage_out=usage,
            )
            async for event in agen:
                if event[0] == "image":
                    break
            await agen.aclose()

        # 只发出并记了第一张；第二张未被生成/转存
        record.assert_awaited_once_with("wanx", 1, 0.2)
        self.assertEqual(usage["images_cost"], 0.2)

    async def test_rejects_non_dashscope_host(self):
        with self.assertRaises(Exception):
            await image_pipeline._download_image("https://evil.example.com/a.png")

    async def test_custom_provider_uses_public_host_boundary(self):
        """自定义图床 CDN 域名不固定：下载走公网 https 校验，而不是 aliyuncs 白名单。"""
        provider = FakeImageProvider(["https://cdn.example.com/a.png"])
        provider.provider_name = "custom_image"
        download = mock.AsyncMock(return_value=(PNG, "image/png", 1, 1))
        with mock.patch.object(image_pipeline.cos_service, "is_configured", return_value=True), \
             mock.patch.object(image_pipeline, "_download_image", new=download), \
             mock.patch.object(image_pipeline.cos_service, "upload_bytes",
                               new=mock.AsyncMock(return_value="https://cdn.example.com/a.png")), \
             mock.patch.object(image_pipeline.ai_usage_service, "record_image_usage",
                               new=mock.AsyncMock()):
            _ = [e async for e in image_pipeline.iter_images(
                ImageRequest(prompt="一只猫", count=1), provider=provider,
            )]
        download.assert_awaited_once_with("https://cdn.example.com/a.png", allow_public=True)

    def test_public_host_guard_blocks_private_networks(self):
        with mock.patch.object(
            image_pipeline.socket, "getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
        ):
            self.assertFalse(image_pipeline._host_is_public("cdn.example.com"))
        with mock.patch.object(
            image_pipeline.socket, "getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ):
            self.assertTrue(image_pipeline._host_is_public("cdn.example.com"))
        self.assertFalse(image_pipeline._host_is_public("localhost"))

    async def test_accepts_data_url_from_custom_provider(self):
        url = "data:image/png;base64," + base64.b64encode(PNG).decode()
        with (
            mock.patch.object(image_pipeline, "is_allowed_image", return_value=True),
            mock.patch.object(image_pipeline, "validate_image_pixels", return_value=(1, 1)),
        ):
            data, content_type, width, height = await image_pipeline._download_image(url)
        self.assertEqual(data, PNG)
        self.assertEqual(content_type, "image/png")
        self.assertEqual((width, height), (1, 1))
