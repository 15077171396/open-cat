"""图库导入流水线与缩略图签名代理契约。"""

import io
import unittest
from functools import partial
from pathlib import Path
from unittest import mock

import httpx
from PIL import Image

from app.core.exceptions import AppException
from app.models.image_asset import ImageAsset
from app.services.image_search import importer, thumb
from app.services.storage import StorageError, storage
from app.services.image_search.base import ImageHit
from tests.fake_db import MemorySession

PNG_TYPE = "image/png"


def _png(width=20, height=10, color=(255, 0, 0), mode="RGB"):
    image = Image.new(mode, (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _hit(url="https://images.pexels.com/a.jpg", source="pexels"):
    return ImageHit(
        source=source, source_id="1", thumb_url=url, download_url=url,
        width=20, height=10, alt="cat", title="cat", author="Alice",
        author_url="https://www.pexels.com/@alice",
        page_url="https://www.pexels.com/photo/1/",
        license="Pexels License", license_url="https://www.pexels.com/license/",
    )


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_200_unavailable_placeholder_is_rejected(self):
        data = (Path(__file__).parent / "fixtures" / "qpic-unavailable.png").read_bytes()
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, content=data, headers={"content-type": "image/png"},
        ))
        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True):
            with self.assertRaises(AppException) as ctx:
                await importer.download_image(
                    "https://qqpublic.qpic.cn/qq_public/placeholder",
                    allow_public=True, max_bytes=1024 * 1024, transport=transport,
                )
        # 占位图是「图源侧已失效」，不是网关错误
        self.assertEqual(ctx.exception.code, 404)

    async def test_ordinary_120px_image_is_not_filtered_by_dimensions(self):
        data = _png(120, 120)
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, content=data, headers={"content-type": "image/png"},
        ))
        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True):
            result, content_type = await importer.download_image(
                "https://qqpublic.qpic.cn/qq_public/valid",
                allow_public=True, max_bytes=1024 * 1024, transport=transport,
            )
        self.assertEqual(result, data)
        self.assertEqual(content_type, "image/png")

    async def test_downloads_png(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "images.pexels.com"
            return httpx.Response(200, content=_png(), headers={"content-type": PNG_TYPE})

        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True):
            data, content_type = await importer.download_image(
                "https://images.pexels.com/a.jpg",
                allowed_hosts=("images.pexels.com",), max_bytes=1024 * 1024,
                transport=httpx.MockTransport(handler),
            )
        self.assertEqual(content_type, PNG_TYPE)
        self.assertEqual(data[:8], bytes.fromhex("89504e470d0a1a0a"))

    async def test_rejects_non_allowlisted_host(self):
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, content=_png())

        with self.assertRaises(AppException) as ctx:
            await importer.download_image(
                "https://evil.example.com/a.png",
                allowed_hosts=("images.pexels.com",), max_bytes=1024,
                transport=httpx.MockTransport(handler),
            )
        self.assertEqual(ctx.exception.code, 502)
        self.assertFalse(called)

    async def test_rejects_redirect_to_other_host(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://evil.example.com/a.png"})

        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True):
            with self.assertRaises(AppException) as ctx:
                await importer.download_image(
                    "https://images.pexels.com/a.png",
                    allowed_hosts=("images.pexels.com",), max_bytes=1024,
                    transport=httpx.MockTransport(handler),
                )
        self.assertEqual(ctx.exception.code, 502)

    async def test_rejects_oversize_by_content_length(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=_png(), headers={
                    "content-type": PNG_TYPE, "content-length": "999999",
                },
            )

        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True):
            with self.assertRaises(AppException) as ctx:
                await importer.download_image(
                    "https://images.pexels.com/a.png",
                    allowed_hosts=("images.pexels.com",), max_bytes=1024,
                    transport=httpx.MockTransport(handler),
                )
        self.assertEqual(ctx.exception.code, 502)

    async def test_public_http_source_is_allowed_only_when_enabled(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_png(), headers={"content-type": PNG_TYPE})

        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True):
            data, content_type = await importer.download_image(
                "http://example.com/a.png",
                max_bytes=1024 * 1024,
                allow_public=True,
                allow_http=True,
                transport=httpx.MockTransport(handler),
            )
        self.assertEqual(content_type, PNG_TYPE)
        self.assertEqual(data[:8], bytes.fromhex("89504e470d0a1a0a"))

    async def test_timeout_is_504(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timeout", request=request)

        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True):
            with self.assertRaises(AppException) as ctx:
                await importer.download_image(
                    "https://images.pexels.com/a.png",
                    allowed_hosts=("images.pexels.com",), max_bytes=1024,
                    transport=httpx.MockTransport(handler),
                )
        self.assertEqual(ctx.exception.code, 504)


class NormalizeTests(unittest.TestCase):
    def test_png_to_webp_and_resize(self):
        prepared = importer.normalize_image(_png(3000, 1000), PNG_TYPE)
        self.assertTrue(prepared.data.startswith(b"RIFF"))
        self.assertEqual(prepared.data[8:12], b"WEBP")
        self.assertEqual((prepared.width, prepared.height), (2400, 800))
        self.assertEqual(prepared.ext, ".webp")

    def test_animated_gif_is_rejected(self):
        frames = [
            Image.new("RGB", (10, 10), (255, 0, 0)),
            Image.new("RGB", (10, 10), (0, 255, 0)),
        ]
        buffer = io.BytesIO()
        frames[0].save(
            buffer, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0,
        )
        with self.assertRaises(ValueError):
            importer.normalize_image(buffer.getvalue(), "image/gif")
        prepared = importer.normalize_image(
            buffer.getvalue(), "image/gif", allow_animation=True,
        )
        self.assertEqual(prepared.content_type, "image/gif")

    def test_invalid_bytes_are_rejected(self):
        with self.assertRaises(ValueError):
            importer.normalize_image(b"not an image", PNG_TYPE)


class ImportHitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.download = mock.AsyncMock(return_value=(_png(), PNG_TYPE))
        self.upload = mock.AsyncMock(return_value="https://cdn.example/stock.webp")
        self.patches = [
            mock.patch.object(importer, "download_image", self.download),
            mock.patch.object(importer.storage, "is_configured", return_value=True),
            mock.patch.object(importer.storage, "upload_bytes", self.upload),
        ]
        for patch in self.patches:
            patch.start()

    async def asyncTearDown(self):
        for patch in self.patches:
            patch.stop()

    async def test_import_and_dedupe(self):
        asset = await importer.import_hit(
            self.db, _hit(), alt=" 一只猫 ", query="cat", admin_id=7,
        )
        self.assertEqual(asset.source, "pexels")
        self.assertEqual(asset.source_id, "1")
        self.assertEqual(asset.alt, "一只猫")
        self.assertEqual(asset.width, 20)
        self.assertEqual(asset.height, 10)
        self.assertEqual(asset.thumb_url, "https://cdn.example/stock.webp")
        self.assertEqual(asset.created_by, 7)
        self.assertEqual(self.download.await_count, 1)
        self.upload.assert_awaited_once()
        folder = self.upload.await_args.kwargs["folder"]
        self.assertEqual(folder, "images/pexels")

        again = await importer.import_hit(
            self.db, _hit(), alt=None, query="cat", admin_id=7,
        )
        self.assertEqual(again.id, asset.id)
        self.assertEqual(self.download.await_count, 1)

    async def test_import_falls_back_to_thumbnail(self):
        hit = _hit(url="https://images.pexels.com/a.jpg")
        hit = ImageHit(**{**hit.__dict__, "fallback_urls": ("https://images.pexels.com/t.jpg",)})
        self.download.side_effect = [
            AppException(code=502, message="原图 403"),
            (_png(), PNG_TYPE),
        ]
        asset = await importer.import_hit(
            self.db, hit, alt="cat", query="cat", admin_id=7,
        )
        self.assertEqual(asset.source, "pexels")
        self.assertEqual(self.download.await_count, 2)

    async def test_cos_not_configured_rejects_before_download(self):
        importer.storage.is_configured.return_value = False
        with self.assertRaises(AppException) as ctx:
            await importer.import_hit(self.db, _hit(), alt=None, query="", admin_id=1)
        self.assertEqual(ctx.exception.code, 503)
        self.download.assert_not_awaited()

    async def test_cos_failure_is_503(self):
        importer.storage.upload_bytes.side_effect = importer.StorageError("boom")
        with self.assertRaises(AppException) as ctx:
            await importer.import_hit(self.db, _hit(), alt=None, query="", admin_id=1)
        self.assertEqual(ctx.exception.code, 503)

    async def test_local_hit_returns_existing_asset(self):
        self.db.add(ImageAsset(
            source="upload", source_id="abc", sha256="abc",
            cos_url="https://cos.example/a.webp", width=10, height=10,
            is_active=True,
        ))
        await self.db.commit()
        local_hit = ImageHit(
            source="local", source_id="1", thumb_url="", download_url="",
            width=10, height=10,
        )
        asset = await importer.import_hit(
            self.db, local_hit, alt=None, query="", admin_id=1,
        )
        self.assertEqual(asset.cos_url, "https://cos.example/a.webp")
        self.download.assert_not_awaited()


class PlaceholderPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_placeholder_cannot_be_imported_or_returned_by_thumbnail_proxy(self):
        data = (Path(__file__).parent / "fixtures" / "qpic-unavailable.png").read_bytes()
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, content=data, headers={"content-type": "image/png"},
        ))
        downloader = partial(importer.download_image, transport=transport)
        url = "https://qqpublic.qpic.cn/qq_public/placeholder"
        db = MemorySession()
        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True), \
                mock.patch.object(importer, "download_image", new=downloader), \
                mock.patch.object(importer.storage, "is_configured", return_value=True), \
                mock.patch.object(importer.storage, "upload_bytes", new=mock.AsyncMock()) as upload:
            with self.assertRaises(AppException):
                await importer.import_hit(db, _hit(url, "sogou"), alt="熊猫", query="熊猫", admin_id=4)
            with self.assertRaises(AppException):
                await thumb.fetch_thumb(thumb.ThumbTarget(url, "sogou"), transport=transport)
        upload.assert_not_awaited()
        self.assertEqual(db.commits, 0)

    async def test_real_image_fallback_still_imports_when_primary_is_placeholder(self):
        placeholder = (Path(__file__).parent / "fixtures" / "qpic-unavailable.png").read_bytes()
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, content=placeholder if request.url.path == "/bad" else _png(),
            headers={"content-type": "image/png"},
        ))
        downloader = partial(importer.download_image, transport=transport)
        hit = ImageHit(
            source="sogou", source_id="placeholder", width=20, height=10,
            thumb_url="https://qqpublic.qpic.cn/good", download_url="https://qqpublic.qpic.cn/bad",
            fallback_urls=("https://qqpublic.qpic.cn/good",),
        )
        with mock.patch("app.services.image_search.policy.is_public_host", return_value=True), \
                mock.patch.object(importer, "download_image", new=downloader), \
                mock.patch.object(importer.storage, "is_configured", return_value=True), \
                mock.patch.object(importer.storage, "upload_bytes", new=mock.AsyncMock(
                    return_value="https://cos.example/valid.webp",
                )):
            asset = await importer.import_hit(MemorySession(), hit, alt="熊猫", query="熊猫", admin_id=4)
        self.assertEqual((asset.width, asset.height), (20, 10))
        self.assertEqual(asset.cos_url, "https://cos.example/valid.webp")


class ThumbTests(unittest.IsolatedAsyncioTestCase):
    async def test_sign_verify_roundtrip(self):
        url = "https://images.pexels.com/a.jpg"
        token = thumb.sign_thumb(url)
        self.assertEqual(thumb.verify_thumb(token).url, url)

    async def test_tampered_token_is_rejected(self):
        token = thumb.sign_thumb("https://images.pexels.com/a.jpg")
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        with self.assertRaises(AppException) as ctx:
            thumb.verify_thumb(tampered)
        self.assertEqual(ctx.exception.code, 403)

    async def test_expired_token_is_rejected(self):
        token = thumb.sign_thumb("https://images.pexels.com/a.jpg", expires_in=-1)
        with self.assertRaises(AppException) as ctx:
            thumb.verify_thumb(token)
        self.assertEqual(ctx.exception.code, 403)

    async def test_signature_does_not_bypass_host_allowlist(self):
        token = thumb.sign_thumb("https://evil.example.com/a.jpg")
        with self.assertRaises(AppException) as ctx:
            thumb.verify_thumb(token)
        self.assertEqual(ctx.exception.code, 403)

    async def test_fetch_thumb_uses_cap(self):
        with mock.patch.object(thumb, "download_image", new=mock.AsyncMock(
            return_value=(b"x", PNG_TYPE)
        )) as download:
            await thumb.fetch_thumb(thumb.ThumbTarget("https://images.pexels.com/a.jpg"))
        self.assertEqual(download.await_args.kwargs["max_bytes"], thumb.THUMB_MAX_BYTES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
