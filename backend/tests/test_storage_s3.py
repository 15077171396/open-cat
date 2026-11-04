"""S3 兼容存储驱动（s3_provider）的离线契约测试。

钉四类不变量（全程不碰真桶 —— SDK 边界一律打桩，与 test_storage.py 测 local 的口径一致）：

1. **配置门控**：三元组缺失时 `is_configured()` 为 False，上传直接 StorageError；
2. **公开 URL 拼接**：CDN 前缀优先；未配时按寻址风格拼端点域名（path/virtual）、
   AWS 官方域名（无端点）；`public_url_prefixes()` 是图片白名单校验的数据源，
   拼错的表现是「上传成功但被自己的图片代理判为外站」；
3. **寻址风格校验**：非法值显式拒绝（传给 botocore 之前拦住）；
4. **客户端指纹缓存**：配置（密钥/端点/风格）变更后自动重建 ——
   runtime_config 会热覆盖 settings，缓存不失效就会拿旧密钥继续上传。
"""

import unittest
from unittest import mock

from app.config import settings
from app.services import storage as storage_pkg
from app.services.storage import StorageError, storage
from app.services.storage import s3_provider


class _S3SettingsMixin:
    """把驱动固定为 s3、凭据指向假值；用例按需再覆盖单个键。"""

    def setUp(self):
        super().setUp()
        self._patcher = mock.patch.multiple(
            settings,
            STORAGE_DRIVER="s3",
            STORAGE_S3_ENDPOINT="https://minio.example.com",
            STORAGE_S3_REGION="",
            STORAGE_S3_ACCESS_KEY="ak-test",
            STORAGE_S3_SECRET_KEY="sk-test",
            STORAGE_S3_BUCKET="cat-media",
            STORAGE_S3_PUBLIC_BASE="",
            STORAGE_S3_ADDRESSING="auto",
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        # 门面按 lru_cache 记住驱动；换配置后必须显式清掉（与 test_storage 同款守卫）
        storage_pkg._load_provider.cache_clear()
        self.addCleanup(storage_pkg._load_provider.cache_clear)
        s3_provider._reset_client_cache()
        self.addCleanup(s3_provider._reset_client_cache)


class ConfigGateTests(_S3SettingsMixin, unittest.TestCase):
    def test_not_configured_when_credentials_missing(self):
        with mock.patch.object(settings, "STORAGE_S3_SECRET_KEY", ""):
            self.assertFalse(s3_provider.is_configured())
            self.assertFalse(storage.is_configured())

    def test_configured_when_triple_present(self):
        self.assertTrue(s3_provider.is_configured())
        self.assertTrue(storage.is_configured())

    def test_upload_refuses_without_credentials(self):
        import asyncio

        with mock.patch.object(settings, "STORAGE_S3_ACCESS_KEY", ""):
            s3_provider._reset_client_cache()
            with self.assertRaises(StorageError) as ctx:
                asyncio.run(
                    storage.upload_bytes(
                        b"x", folder="images", ext=".png", content_type="image/png"
                    )
                )
        self.assertIn("S3 未配置", str(ctx.exception))

    def test_invalid_addressing_style_refused(self):
        import asyncio

        with mock.patch.object(settings, "STORAGE_S3_ADDRESSING", "weird"):
            s3_provider._reset_client_cache()
            with self.assertRaises(StorageError) as ctx:
                asyncio.run(
                    storage.upload_bytes(
                        b"x", folder="images", ext=".png", content_type="image/png"
                    )
                )
        self.assertIn("STORAGE_S3_ADDRESSING", str(ctx.exception))


class PublicUrlTests(_S3SettingsMixin, unittest.TestCase):
    def test_public_base_wins(self):
        with mock.patch.object(settings, "STORAGE_S3_PUBLIC_BASE", "https://cdn.example.com/media"):
            self.assertEqual(
                s3_provider.public_url_prefixes(),
                ("https://cdn.example.com/media/",),
            )
            self.assertEqual(
                s3_provider._build_public_url("images/2026/11/04/abc.png"),
                "https://cdn.example.com/media/images/2026/11/04/abc.png",
            )

    def test_path_style_uses_endpoint_host_and_bucket_in_path(self):
        with mock.patch.object(settings, "STORAGE_S3_ADDRESSING", "path"):
            self.assertEqual(
                s3_provider.public_url_prefixes(),
                ("https://minio.example.com/cat-media/",),
            )

    def test_virtual_style_puts_bucket_in_host(self):
        with mock.patch.object(settings, "STORAGE_S3_ADDRESSING", "virtual"):
            self.assertEqual(
                s3_provider.public_url_prefixes(),
                ("https://cat-media.minio.example.com/",),
            )

    def test_aws_default_domain_when_no_endpoint(self):
        with mock.patch.object(settings, "STORAGE_S3_ENDPOINT", ""), \
             mock.patch.object(settings, "STORAGE_S3_REGION", "ap-northeast-1"):
            self.assertEqual(
                s3_provider.public_url_prefixes(),
                ("https://cat-media.s3.ap-northeast-1.amazonaws.com/",),
            )

    def test_prefixes_empty_on_bad_style(self):
        """寻址风格非法时前缀返回空而不是抛错：图片白名单校验拿它做「否定证据」，
        抛错会把本可以走 local 前缀校验的请求一起炸掉。"""
        with mock.patch.object(settings, "STORAGE_S3_ADDRESSING", "weird"):
            self.assertEqual(s3_provider.public_url_prefixes(), ())


class _FakeBotoClient:
    """记录调用的假 S3 客户端（SDK 边界打桩）"""

    def __init__(self, *, acl_error: Exception | None = None):
        self.puts: list[dict] = []
        self.acl_calls: list[dict] = []
        self._acl_error = acl_error

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {"ETag": '"fake"'}

    def put_object_acl(self, **kwargs):
        if self._acl_error is not None:
            raise self._acl_error
        self.acl_calls.append(kwargs)

    def get_object(self, **kwargs):
        class _Body:
            def read(self):
                return b"object-bytes"

        return {"ContentLength": 12, "ContentType": "image/png; charset=binary", "Body": _Body()}


class UploadDownloadTests(_S3SettingsMixin, unittest.TestCase):
    def _fake_client(self, **kwargs) -> _FakeBotoClient:
        fake = _FakeBotoClient(**kwargs)
        patcher = mock.patch.object(s3_provider, "_get_client", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_upload_passes_key_content_type_and_returns_url(self):
        fake = self._fake_client()
        import asyncio

        url = asyncio.run(
            storage.upload_bytes(
                b"hello", folder="images", ext=".png", content_type="image/png"
            )
        )
        self.assertEqual(len(fake.puts), 1)
        call = fake.puts[0]
        self.assertEqual(call["Bucket"], "cat-media")
        self.assertEqual(call["ContentType"], "image/png")
        # key 规则与 local/cos 驱动一致：folder/年/月/日/uuid
        self.assertTrue(call["Key"].startswith("images/"), call["Key"])
        self.assertTrue(call["Key"].endswith(".png"))
        # 默认域名按端点 + auto 风格拼（botocore auto 对域名型端点选 virtual）
        self.assertTrue(url.startswith("https://"), url)
        # 上传后补 ACL（幂等，失败只告警）
        self.assertEqual(fake.acl_calls[0]["ACL"], "public-read")

    def test_upload_survives_acl_failure(self):
        """ACL 被桶禁用（现代默认）时报错不能阻断上传 —— 内容已写入。"""
        from botocore.exceptions import ClientError

        fake = self._fake_client(
            acl_error=ClientError({"Error": {"Code": "AccessControlListNotSupported"}}, "PutObjectAcl")
        )
        import asyncio

        url = asyncio.run(
            storage.upload_bytes(
                b"hello", folder="images", ext=".png", content_type="image/png"
            )
        )
        self.assertTrue(url)
        self.assertEqual(len(fake.puts), 1)
        self.assertEqual(fake.acl_calls, [], "ACL 失败时调用记为空，只走告警路径")

    def test_download_returns_content_and_type(self):
        self._fake_client()
        import asyncio

        data, content_type = asyncio.run(storage.download_object_bytes("images/x.png", 1024))
        self.assertEqual(data, b"object-bytes")
        self.assertEqual(content_type, "image/png")

    def test_download_rejects_oversize_before_reading(self):
        fake = self._fake_client()

        class _Boom:
            def read(self):  # pragma: no cover - 超限后不应被调用
                raise AssertionError("超限对象不应读出 Body")

        fake.get_object = lambda **kw: {"ContentLength": 9999, "Body": _Boom()}
        import asyncio

        with self.assertRaises(StorageError) as ctx:
            asyncio.run(storage.download_object_bytes("images/big.png", 1024))
        self.assertIn("中转上限", str(ctx.exception))


class ClientCacheTests(_S3SettingsMixin, unittest.TestCase):
    def test_client_rebuilt_when_fingerprint_changes(self):
        """runtime_config 热覆盖密钥后，指纹变化必须触发重建（否则拿旧密钥继续传）。

        boto3 导入太重，对 `_create_client` 打桩计数即可 —— 缓存逻辑在 _get_client 里，
        与 SDK 无关。
        """
        with mock.patch.object(s3_provider, "_create_client", return_value=_FakeBotoClient()) as create:
            s3_provider._reset_client_cache()
            s3_provider._get_client()
            s3_provider._get_client()
            self.assertEqual(create.call_count, 1, "指纹不变时复用客户端")

            with mock.patch.object(settings, "STORAGE_S3_SECRET_KEY", "sk-rotated"):
                s3_provider._get_client()
                self.assertEqual(create.call_count, 2, "密钥变更后必须重建客户端")


class FacadeRegistrationTests(unittest.TestCase):
    def test_unknown_driver_error_lists_s3(self):
        import asyncio

        with mock.patch.object(settings, "STORAGE_DRIVER", "s3-ish"):
            storage_pkg._load_provider.cache_clear()
            try:
                with self.assertRaises(StorageError) as ctx:
                    asyncio.run(
                        storage.upload_bytes(
                            b"x", folder="images", ext=".png", content_type="image/png"
                        )
                    )
            finally:
                storage_pkg._load_provider.cache_clear()
        self.assertIn("s3", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
