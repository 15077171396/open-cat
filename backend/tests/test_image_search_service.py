"""搜索编排契约：缓存、熔断、额度、本站素材与配置回退。"""

import time
import unittest
from unittest import mock

from app.config import settings
from app.core.crypto import encrypt_secret
from app.core.exceptions import AppException
from app.models.image_asset import ImageAsset
from app.models.system_setting import SystemSetting
from app.services.image_search import config as image_config
from app.services.image_search import service
from app.services.image_search.base import ImageHit, SearchPage
from app.services.image_search.config import ImageSearchConfig
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def _cfg(**overrides):
    data = {
        "enabled": True,
        "page_size": 24,
        "hourly_limit": 120,
        "daily_import_limit": 200,
        "keys": {"pexels": "pexels-key", "pixabay": "pixabay-key"},
    }
    data.update(overrides)
    return ImageSearchConfig(**data)


def _hit(source="pexels", source_id="1"):
    return ImageHit(
        source=source, source_id=source_id,
        thumb_url="https://images.pexels.com/t.jpg",
        download_url="https://images.pexels.com/d.jpg",
        width=1200, height=800, alt="cat", author="Alice",
        page_url="https://www.pexels.com/photo/1/",
        license="Pexels License", license_url="https://www.pexels.com/license/",
    )


class FakeProvider:
    def __init__(self, hits=None, error=None):
        self.hits = hits or [_hit()]
        self.error = error
        self.calls = 0
        self.closed = False

    async def search(self, query):
        self.calls += 1
        if self.error:
            raise self.error
        return SearchPage(items=self.hits, total=100, has_more=True)

    async def get(self, source_id):
        self.calls += 1
        return self.hits[0]

    async def close(self):
        self.closed = True


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = FakeRedis()
        self.patches = [
            mock.patch.object(service, "redis_client", self.redis),
            mock.patch.object(service, "load_config", new=mock.AsyncMock(return_value=_cfg())),
        ]
        for patch in self.patches:
            patch.start()

    async def asyncTearDown(self):
        for patch in self.patches:
            patch.stop()

    async def _search(self, **overrides):
        data = {
            "admin_id": 1, "source": "pexels", "q": "cat", "page": 1,
            "per_page": 12, "image_type": "photo", "orientation": "all",
        }
        data.update(overrides)
        return await service.search(MemorySession(), **data)

    async def test_cache_hit_skips_provider(self):
        provider = FakeProvider()
        with mock.patch.object(service, "build_provider", return_value=provider):
            first = await self._search()
            second = await self._search()
        self.assertEqual(provider.calls, 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["items"][0]["source_id"], "1")

    async def test_provider_failure_opens_breaker(self):
        provider = FakeProvider(error=AppException(code=502, message="boom"))
        with mock.patch.object(service, "build_provider", return_value=provider):
            for _ in range(3):
                with self.assertRaises(AppException) as ctx:
                    await self._search()
                self.assertEqual(ctx.exception.code, 502)
            with self.assertRaises(AppException) as ctx:
                await self._search()
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(provider.calls, 3)

    async def test_provider_quota_is_enforced(self):
        provider = FakeProvider()
        key = f"image_search:provider:pexels:{int(time.time() // 3600)}"
        await self.redis.setex(key, 3600, "180")
        with mock.patch.object(service, "build_provider", return_value=provider):
            with self.assertRaises(AppException) as ctx:
                await self._search()
        self.assertEqual(ctx.exception.code, 429)
        self.assertEqual(provider.calls, 0)

    async def test_disabled_rejects(self):
        service.load_config.return_value = _cfg(enabled=False)
        with self.assertRaises(AppException) as ctx:
            await self._search()
        self.assertEqual(ctx.exception.code, 503)

    async def test_missing_key_rejects(self):
        service.load_config.return_value = _cfg(keys={"pexels": "", "pixabay": ""})
        with self.assertRaises(AppException) as ctx:
            await self._search()
        self.assertEqual(ctx.exception.code, 503)

    async def test_local_search(self):
        service.load_config.return_value = _cfg()
        db = MemorySession()
        db.add(ImageAsset(
            source="upload", source_id="abc", sha256="abc",
            title="猫咪配图", alt="一只猫", cos_url="https://cos.example/cat.webp",
            width=800, height=600, is_active=True,
        ))
        await db.commit()
        result = await service.search(
            db, admin_id=1, source="local", q="猫", page=1, per_page=12,
            image_type="all", orientation="all",
        )
        self.assertEqual(result["source"], "local")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["preview_url"], "https://cos.example/cat.webp")

    async def test_source_toggles_are_respected(self):
        cfg = _cfg(
            enabled_sources={"pexels": False, "pixabay": True},
            keys={"pexels": "k", "pixabay": "k"},
        )
        self.assertFalse(cfg.has_source("pexels"))
        self.assertTrue(cfg.has_source("pixabay"))

    async def test_import_quota_counts_successful_imports(self):
        cfg = _cfg(daily_import_limit=1)
        await service.check_import_quota(cfg, 1)
        await service.record_import_success(1)
        with self.assertRaises(AppException) as ctx:
            await service.check_import_quota(cfg, 1)
        self.assertEqual(ctx.exception.code, 429)

    async def test_admin_hourly_limit(self):
        service.load_config.return_value = _cfg(hourly_limit=1)
        provider = FakeProvider()
        with mock.patch.object(service, "build_provider", return_value=provider):
            await self._search()
            with self.assertRaises(AppException) as ctx:
                await self._search()
        self.assertEqual(ctx.exception.code, 429)


class LoadConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_env_fallback(self):
        class Result:
            def scalars(self):
                return self

            def all(self):
                return []

        db = mock.Mock()
        db.execute = mock.AsyncMock(return_value=Result())
        with mock.patch.object(settings, "IMAGE_SEARCH_ENABLED", True), \
                mock.patch.object(settings, "IMAGE_SEARCH_PEXELS_KEY", "env-pexels"), \
                mock.patch.object(settings, "IMAGE_SEARCH_PIXABAY_KEY", "env-pixabay"):
            cfg = await image_config.load_config(db)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.keys["pexels"], "env-pexels")
        self.assertEqual(cfg.keys["pixabay"], "env-pixabay")

    async def test_db_secret_wins_and_disable(self):
        class Result:
            def scalars(self):
                return self

            def all(self):
                return [
                    SystemSetting(setting_key="image_search_enabled", value="0"),
                    SystemSetting(setting_key="image_search_pexels_key", value=encrypt_secret("db-key")),
                ]

        db = mock.Mock()
        db.execute = mock.AsyncMock(return_value=Result())
        with mock.patch.object(settings, "IMAGE_SEARCH_ENABLED", True), \
                mock.patch.object(settings, "IMAGE_SEARCH_PEXELS_KEY", "env-key"), \
                mock.patch.object(settings, "IMAGE_SEARCH_PIXABAY_KEY", ""):
            cfg = await image_config.load_config(db)
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.keys["pexels"], "db-key")
        self.assertEqual(cfg.keys["pixabay"], "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
