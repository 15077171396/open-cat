"""COS 客户端初始化的异常出口契约。

`_get_client()` 的文档承诺「抛 CosError 由调用方返回 500」，但 SDK 的延迟导入与构造语句
原先落在 try 之外：镜像少装依赖 / SDK 构造报错时抛的是 ModuleNotFoundError、ValueError 之类，
四个调用点（评论配图、编辑器图片、资源文件、头像）只 `except CosError`，于是全部穿透成
全局兜底 500「服务器内部错误」——既丢掉「COS 用不了」这个可定位语义，日志里也只剩一行
无路径的未处理异常（2026-10-13 排障时确认过：兜底日志不带 traceback 之外的定位信息）。
本文件把「只可能抛 CosError」钉成断言。
"""

import sys
import unittest
from unittest import mock

from app.config import settings
from app.services import cos_service


class CosClientInitErrorTests(unittest.TestCase):
    def setUp(self):
        self._orig_client = cos_service._client
        cos_service._client = None

    def tearDown(self):
        cos_service._client = self._orig_client

    def _upload(self):
        return cos_service._upload_sync("comments/test.png", b"x", "image/png")

    def _cos_configured(self):
        """临时补齐 COS 配置。

        本文件验证的是「SDK 层异常能否收进 CosError」，`_get_client()` 的第一道检查却是
        「配置是否存在」——未配置环境会先抛「COS 未配置」，永远走不到 SDK 分支，
        断言 "初始化失败" 必然落空。CI 与本地开发都没有 COS 凭据，故必须在这里补上，
        否则这两项**在任何未配置 COS 的环境里都会失败**（会长期污染 CI 信号）。
        """
        return mock.patch.multiple(
            settings,
            COS_SECRET_ID="test-secret-id",
            COS_SECRET_KEY="test-secret-key",
            COS_BUCKET="test-1250000000",
        )

    def test_missing_sdk_surfaces_as_coserror(self):
        # sys.modules 里为 None 会让 import 抛 ModuleNotFoundError：等同「镜像没装 cos-python-sdk-v5」
        with self._cos_configured(), mock.patch.dict(sys.modules, {"qcloud_cos": None}):
            with self.assertRaises(cos_service.CosError) as ctx:
                self._upload()
        self.assertIn("初始化失败", str(ctx.exception))
        self.assertIn("ModuleNotFoundError", str(ctx.exception), "必须带上底层异常类型，否则缺包与配置错无法区分")

    def test_sdk_construction_error_surfaces_as_coserror(self):
        import qcloud_cos

        with self._cos_configured(), mock.patch.object(qcloud_cos, "CosConfig", side_effect=ValueError("bad region")):
            with self.assertRaises(cos_service.CosError) as ctx:
                self._upload()
        self.assertIn("ValueError", str(ctx.exception), "必须带上底层异常类型，否则缺包与配置错无法区分")

    def test_unconfigured_still_reports_config_error(self):
        with mock.patch.object(settings, "COS_SECRET_ID", ""), \
                mock.patch.object(settings, "COS_SECRET_KEY", ""), \
                mock.patch.object(settings, "COS_BUCKET", ""):
            with self.assertRaises(cos_service.CosError) as ctx:
                self._upload()
        self.assertIn("COS 未配置", str(ctx.exception))

    def test_successful_init_is_not_swallowed(self):
        """反向对照：try 包住的必须是构造，不能把成功路径一起吞成 CosError。"""
        with mock.patch.object(settings, "COS_SECRET_ID", "sid"), \
                mock.patch.object(settings, "COS_SECRET_KEY", "skey"), \
                mock.patch.object(settings, "COS_BUCKET", "bkt-123"):
            client = cos_service._get_client()
        self.assertEqual(type(client).__name__, "CosS3Client")
        self.assertIs(cos_service._client, client, "单例必须真的缓存住，否则每次上传都重建")

    def test_client_is_isolated_per_thread(self):
        """非主线程必须拿到独立客户端实例

        `cos-python-sdk-v5` 的 CosS3Client 非线程安全：共享实例做并发 put_object 时
        `ACL: public-read` 头会被偶发丢掉，对象变私有 → 浏览器匿名访问 403
        （2026-10-19 实测：4 路并发共享实例 3/4 不可读，每线程独立后 4/4 可读）。
        这里把「每线程一个实例」钉成契约，避免有人把缓存改回全局单例。
        """
        from concurrent.futures import ThreadPoolExecutor

        with self._cos_configured():
            main_client = cos_service._get_client()
            with ThreadPoolExecutor(max_workers=2) as pool:
                worker_client = pool.submit(cos_service._get_client).result()
                again = pool.submit(cos_service._get_client).result()
        self.assertIsNot(worker_client, main_client, "工作线程不能复用主线程的客户端")
        self.assertIs(worker_client, again, "同一线程内仍应复用，避免每次上传都重建")


if __name__ == "__main__":
    unittest.main()
