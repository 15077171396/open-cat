"""管理端图片搜索接口：权限规则、代理 URL、导入去重与缩略图响应。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from app.api import admin_image_search, images as images_api
from app.core import rbac
from app.core.exceptions import AppException
from app.services.image_search.config import ImageSearchConfig
from tests.conftest import async_client, make_user


def _cfg(enabled=True):
    return ImageSearchConfig(
        enabled=enabled, page_size=24, hourly_limit=120, daily_import_limit=200,
        keys={"pexels": "k", "pixabay": "k"},
    )


def _asset(source="pexels", author="Alice"):
    return SimpleNamespace(
        id=9, source=source, source_id="p1", cos_url="https://cos.example/a.webp",
        thumb_url="https://cos.example/a.webp", width=1200, height=800,
        alt="cat", author=author, page_url="https://www.pexels.com/photo/1/",
        license="Pexels License", license_url="https://www.pexels.com/license/",
        requires_attribution=False,
    )


class ImageSearchApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_options(self):
        with mock.patch.object(
            admin_image_search.search_service, "build_options",
            new=mock.AsyncMock(return_value={"enabled": True, "sources": []}),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.get("/api/v1/admin/image-search/options")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["data"]["enabled"])

    async def test_search_adds_signed_proxy_urls(self):
        payload = {
            "source": "pexels", "has_more": True, "total": 100, "cached": False,
            "items": [{
                "source": "pexels", "source_id": "1",
                "thumb_url": "https://images.pexels.com/t.jpg",
                "preview_url": "https://images.pexels.com/l.jpg",
            }],
        }
        with mock.patch.object(
            admin_image_search.search_service, "search",
            new=mock.AsyncMock(return_value=payload),
        ), mock.patch.object(
            admin_image_search.thumb_service, "sign_thumb", return_value="token-1",
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.get(
                    "/api/v1/admin/image-search/search",
                    params={"source": "pexels", "q": "cat"},
                )
        item = res.json()["data"]["items"][0]
        self.assertIn("token-1", item["thumb_proxy_url"])
        self.assertIn("token-1", item["preview_proxy_url"])

    async def test_local_items_use_cos_urls_directly(self):
        payload = {
            "source": "local", "has_more": False, "total": None, "cached": False,
            "items": [{
                "source": "local", "source_id": "1",
                "thumb_url": "https://cos.example/a.webp",
                "preview_url": "https://cos.example/a.webp",
            }],
        }
        with mock.patch.object(
            admin_image_search.search_service, "search",
            new=mock.AsyncMock(return_value=payload),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.get(
                    "/api/v1/admin/image-search/search",
                    params={"source": "local"},
                )
        item = res.json()["data"]["items"][0]
        self.assertEqual(item["thumb_proxy_url"], "https://cos.example/a.webp")
        self.assertEqual(item["preview_proxy_url"], "https://cos.example/a.webp")

    async def test_import_deduped_skips_quota_and_download(self):
        asset = _asset()
        with mock.patch.object(
            admin_image_search.search_service, "load_config",
            new=mock.AsyncMock(return_value=_cfg()),
        ), mock.patch.object(
            admin_image_search.importer_service, "find_asset",
            new=mock.AsyncMock(return_value=asset),
        ), mock.patch.object(
            admin_image_search.search_service, "check_import_quota",
            new=mock.AsyncMock(),
        ) as quota, mock.patch.object(
            admin_image_search.search_service, "record_import_success",
            new=mock.AsyncMock(),
        ) as record:
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post("/api/v1/admin/image-search/import", json={
                    "source": "pexels", "source_id": "1", "alt": "猫",
                })
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertTrue(data["deduped"])
        self.assertEqual(data["url"], "https://cos.example/a.webp")
        self.assertIn("Alice", data["credit"]["text"])
        quota.assert_not_awaited()
        record.assert_not_awaited()

    async def test_import_new_records_usage(self):
        asset = _asset()
        with mock.patch.object(
            admin_image_search.search_service, "load_config",
            new=mock.AsyncMock(return_value=_cfg()),
        ), mock.patch.object(
            admin_image_search.importer_service, "find_asset",
            new=mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            admin_image_search.search_service, "check_import_quota",
            new=mock.AsyncMock(),
        ) as quota, mock.patch.object(
            admin_image_search.search_service, "resolve_hit",
            new=mock.AsyncMock(return_value=object()),
        ), mock.patch.object(
            admin_image_search.importer_service, "import_hit",
            new=mock.AsyncMock(return_value=asset),
        ), mock.patch.object(
            admin_image_search.search_service, "record_import_success",
            new=mock.AsyncMock(),
        ) as record, mock.patch.object(
            admin_image_search.search_service, "audit_import",
            new=mock.AsyncMock(),
        ) as audit:
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post("/api/v1/admin/image-search/import", json={
                    "source": "pexels", "source_id": "1",
                })
        self.assertFalse(res.json()["data"]["deduped"])
        quota.assert_awaited_once()
        record.assert_awaited_once()
        audit.assert_awaited_once()

    async def test_import_local_asset_is_deduped(self):
        asset = _asset(source="upload", author="")
        with mock.patch.object(
            admin_image_search.search_service, "load_config",
            new=mock.AsyncMock(return_value=_cfg()),
        ), mock.patch.object(
            admin_image_search.search_service, "resolve_local_asset",
            new=mock.AsyncMock(return_value=asset),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post("/api/v1/admin/image-search/import", json={
                    "source": "local", "source_id": "9",
                })
        data = res.json()["data"]
        self.assertTrue(data["deduped"])
        self.assertEqual(data["credit"]["text"], "图片：本站素材")

    async def test_import_disabled(self):
        with mock.patch.object(
            admin_image_search.search_service, "load_config",
            new=mock.AsyncMock(return_value=_cfg(enabled=False)),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post("/api/v1/admin/image-search/import", json={
                    "source": "pexels", "source_id": "1",
                })
        self.assertEqual(res.status_code, 503)

    async def test_thumb_returns_bytes_with_public_cache(self):
        with mock.patch.object(
            images_api.image_search_thumb, "verify_thumb",
            return_value="https://images.pexels.com/a.jpg",
        ), mock.patch.object(
            images_api.image_search_thumb, "fetch_thumb",
            new=mock.AsyncMock(return_value=(b"img", "image/jpeg")),
        ):
            # 模拟浏览器 <img>：不带 Authorization 头也必须能取签名缩略图。
            async with async_client(user=None) as client:
                res = await client.get(
                    "/api/v1/images/search-thumb", params={"token": "x" * 20},
                )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"img")
        self.assertIn("public", res.headers["cache-control"])
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")

    async def test_thumb_rejects_invalid_token(self):
        with mock.patch.object(
            images_api.image_search_thumb, "verify_thumb",
            side_effect=AppException(code=403, message="bad"),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.get(
                    "/api/v1/images/search-thumb", params={"token": "x" * 20},
                )
        self.assertEqual(res.status_code, 403)


class ImageSearchRbacTests(unittest.TestCase):
    def test_routes_use_content_edit(self):
        for path in (
            "/api/v1/admin/image-search/options",
            "/api/v1/admin/image-search/search",
            "/api/v1/admin/image-search/import",
        ):
            method = "POST" if path.endswith("/import") else "GET"
            self.assertEqual(rbac.match_route_permission(method, path), rbac._CONTENT_EDIT_ANY)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
