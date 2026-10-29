"""管理端素材库接口：路由、参数与权限规则。"""

import unittest
from unittest import mock

from app.api import admin_materials
from app.core import rbac
from tests.conftest import async_client, make_user


class MaterialsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_passes_filters(self):
        with mock.patch.object(
            admin_materials.materials_service, "list_materials",
            new=mock.AsyncMock(return_value={
                "items": [], "total": 0, "page": 2, "page_size": 12,
            }),
        ) as service:
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.get("/api/v1/admin/materials", params={
                    "keyword": "猫", "kind": "image", "source": "upload",
                    "page": 2, "page_size": 12,
                })
        self.assertEqual(res.status_code, 200)
        service.assert_awaited_once()
        kwargs = service.await_args.kwargs
        self.assertEqual(kwargs["keyword"], "猫")
        self.assertEqual(kwargs["kind"], "image")
        self.assertEqual(kwargs["page"], 2)

    async def test_stats_and_touch(self):
        with mock.patch.object(
            admin_materials.materials_service, "material_stats",
            new=mock.AsyncMock(return_value={"total": 3, "kinds": {"image": 3}}),
        ), mock.patch.object(
            admin_materials.materials_service, "touch_material",
            new=mock.AsyncMock(return_value=type("A", (), {
                "id": 1, "kind": "image", "source": "upload", "title": "猫",
                "filename": "cat.png", "alt": "猫", "author": "", "mime_type": "image/png",
                "width": 10, "height": 10, "size_bytes": 1,
                "cos_url": "https://cos.example/cat.png", "thumb_url": None,
                "license": "", "page_url": None, "usage_count": 1,
                "last_used_at": None, "created_at": None,
            })()),
        ), mock.patch.object(
            admin_materials.materials_service, "serialize_material",
            return_value={"id": 1, "usage_count": 1},
        ):
            async with async_client(user=make_user(role="admin")) as client:
                stats = await client.get("/api/v1/admin/materials/stats")
                touch = await client.post("/api/v1/admin/materials/1/touch")
        self.assertEqual(stats.json()["data"]["total"], 3)
        self.assertEqual(touch.status_code, 200)
        self.assertEqual(touch.json()["data"]["usage_count"], 1)


class MaterialsRbacTests(unittest.TestCase):
    def test_routes_use_content_edit(self):
        for method, path in (
            ("GET", "/api/v1/admin/materials"),
            ("GET", "/api/v1/admin/materials/stats"),
            ("POST", "/api/v1/admin/materials/1/touch"),
        ):
            self.assertEqual(rbac.match_route_permission(method, path), rbac._CONTENT_EDIT_ANY)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
