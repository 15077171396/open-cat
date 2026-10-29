"""存储门面与本地驱动的契约（开源版新增）。

为什么值得单独测：默认驱动从「只有 COS」变成「默认本机磁盘」，一旦这里错了，
表现是**上传成功但图片 404**（URL 拼错）或**路径穿越可读任意文件**（key 未校验），
两种都不会抛异常，只有人肉点页面或安全审计才发现。

COS 驱动不在这里测：它只是对既有 `cos_service` 的接口对齐，
真正的 COS 行为由 `tests/test_cos_client_init.py` 与线上验证覆盖。
"""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app.config import settings
from app.services import storage as storage_pkg
from app.services.storage import StorageError, get_file_ext, storage
from app.services.storage.base import generate_key
from tests.conftest import async_client, make_user


class _StoragePatchMixin:
    """把驱动固定为 local 并指向临时目录；同步/异步用例共用。"""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self._patcher = mock.patch.multiple(
            settings,
            STORAGE_DRIVER="local",
            STORAGE_LOCAL_DIR=str(self.root),
            STORAGE_PUBLIC_BASE="https://cdn.example.com",
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        # 门面用 lru_cache 记住驱动：换配置后必须显式清掉，否则测的还是上一个驱动的实例
        storage_pkg._load_provider.cache_clear()
        self.addCleanup(storage_pkg._load_provider.cache_clear)


class BaseStorageTestCase(_StoragePatchMixin, unittest.TestCase):
    """同步用例基类。"""


class LocalDriverTests(BaseStorageTestCase):
    def test_upload_writes_file_and_returns_public_url(self):
        import asyncio

        url = asyncio.run(
            storage.upload_bytes(
                b"hello", folder="images", ext=".txt", content_type="text/plain"
            )
        )
        self.assertTrue(
            url.startswith("https://cdn.example.com/uploads/images/"),
            f"URL 前缀不对：{url}",
        )

        # 落盘路径 = 根目录 + 去掉公开前缀的 key
        key = url.split("/uploads/", 1)[1]
        written = self.root / key
        self.assertTrue(written.is_file(), f"文件未落盘：{written}")
        self.assertEqual(written.read_bytes(), b"hello")

    def test_download_returns_content_and_guessed_type(self):
        import asyncio

        url = asyncio.run(
            storage.upload_bytes(
                b"\x89PNG", folder="images", ext=".png", content_type="image/png"
            )
        )
        key = url.split("/uploads/", 1)[1]
        content, content_type = asyncio.run(storage.download_object_bytes(key, 1024))
        self.assertEqual(content, b"\x89PNG")
        self.assertEqual(content_type, "image/png")

    def test_download_rejects_path_traversal(self):
        """key 里带 `..` 必须被拒，否则就是任意文件读取。"""
        import asyncio

        with self.assertRaises(StorageError):
            asyncio.run(storage.download_object_bytes("../outside.txt", 1024))

    def test_download_missing_object_raises(self):
        import asyncio

        with self.assertRaises(StorageError):
            asyncio.run(storage.download_object_bytes("images/nope.png", 1024))

    def test_download_respects_max_size(self):
        import asyncio

        url = asyncio.run(
            storage.upload_bytes(b"x" * 64, folder="files", ext=".txt", content_type="text/plain")
        )
        key = url.split("/uploads/", 1)[1]
        with self.assertRaises(StorageError):
            asyncio.run(storage.download_object_bytes(key, 8))

    def test_public_prefixes_include_base_and_relative(self):
        prefixes = storage.public_url_prefixes()
        self.assertIn("https://cdn.example.com/uploads/", prefixes)
        # 相对前缀兜底：本地开发时 SITE_BASE_URL 可能为空
        self.assertIn("/uploads/", prefixes)

    def test_driver_name_reflects_setting(self):
        self.assertEqual(storage.driver, "local")


class DriverSelectionTests(BaseStorageTestCase):
    def test_unknown_driver_fails_loudly(self):
        """拼错的驱动名不能静默回落到 local，否则「配了云存储却没生效」无从发现。"""
        import asyncio

        with mock.patch.object(settings, "STORAGE_DRIVER", "s3-ish"):
            storage_pkg._load_provider.cache_clear()
            with self.assertRaises(StorageError):
                asyncio.run(
                    storage.upload_bytes(
                        b"x", folder="images", ext=".png", content_type="image/png"
                    )
                )


class KeyAndExtTests(unittest.TestCase):
    def test_generate_key_layout(self):
        key = generate_key("images", ".png")
        parts = key.split("/")
        # images/YYYY/MM/DD/<16 hex>.png
        self.assertEqual(parts[0], "images")
        self.assertEqual(len(parts), 5)
        self.assertTrue(parts[-1].endswith(".png"))
        self.assertEqual(len(parts[-1].split(".")[0]), 16)

    def test_ext_prefers_whitelisted_filename(self):
        self.assertEqual(get_file_ext("photo.JPG", "image/png"), ".jpg")

    def test_ext_drops_dangerous_filename_and_falls_back(self):
        """`evil.php` 不在白名单 → 丢弃文件名，改用 content_type 推断。"""
        self.assertEqual(get_file_ext("evil.php", "image/png"), ".png")
        self.assertEqual(get_file_ext("evil.php", "application/x-httpd-php"), "")
        self.assertEqual(get_file_ext(None, "image/webp"), ".webp")


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 128, 255)).save(buffer, "PNG")
    return buffer.getvalue()


class UploadEndpointWithLocalDriverTests(_StoragePatchMixin, unittest.IsolatedAsyncioTestCase):
    """端到端：默认驱动下「上传接口 → 落盘 → 返回可访问 URL」这条链是通的。

    这条最容易被忽略：单测存储层全绿，但接口层仍可能把 URL 拼成 COS 域名，
    表现为「上传成功、正文配图全裂」。
    """

    async def test_upload_endpoint_writes_file_and_returns_local_url(self):
        from app.api import uploads

        with mock.patch.object(uploads, "record_upload_asset", new=mock.AsyncMock()):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post(
                    "/api/v1/uploads/image",
                    files={"file": ("cat.png", _png(), "image/png")},
                )

        self.assertEqual(res.status_code, 200, res.text)
        url = res.json()["data"]["url"]
        self.assertTrue(
            url.startswith("https://cdn.example.com/uploads/images/"),
            f"URL 未走本地驱动前缀：{url}",
        )
        key = url.split("/uploads/", 1)[1]
        self.assertTrue((self.root / key).is_file(), "文件没有落到 STORAGE_LOCAL_DIR")


if __name__ == "__main__":
    unittest.main()
