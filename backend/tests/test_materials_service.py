"""素材库查询、统计与使用计数。"""

import unittest

from app.core.exceptions import AppException
from app.models.image_asset import ImageAsset
from app.services import materials_service
from tests.fake_db import MemorySession


def _asset(**overrides):
    data = {
        "source": "upload", "source_id": "a1", "sha256": "a1",
        "kind": "image", "mime_type": "image/png", "filename": "cat.png",
        "title": "猫咪配图", "alt": "一只猫",
        "cos_url": "https://cos.example/cat.png",
        "thumb_url": "https://cos.example/cat.png",
        "width": 320, "height": 200, "size_bytes": 1234,
        "is_active": True,
    }
    data.update(overrides)
    return ImageAsset(**data)


class MaterialsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.db.add(_asset())
        self.db.add(_asset(
            source="upload", source_id="f1", sha256="f1", kind="document",
            mime_type="application/pdf", filename="manual.pdf", title="使用手册",
            alt="", cos_url="https://cos.example/manual.pdf", thumb_url=None,
            width=0, height=0, size_bytes=99,
        ))
        await self.db.commit()

    async def test_list_filters_and_keyword(self):
        result = await materials_service.list_materials(self.db, page_size=10)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 2)

        files = await materials_service.list_materials(self.db, kind="document")
        self.assertEqual(files["total"], 1)
        self.assertEqual(files["items"][0]["filename"], "manual.pdf")

        search = await materials_service.list_materials(self.db, keyword="猫咪")
        self.assertEqual(search["total"], 1)
        self.assertEqual(search["items"][0]["kind"], "image")

    async def test_stats_and_touch(self):
        stats = await materials_service.material_stats(self.db)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["kinds"], {"image": 1, "document": 1})

        asset = (await materials_service.list_materials(self.db))["items"][0]
        touched = await materials_service.touch_material(self.db, asset["id"])
        self.assertEqual(touched.usage_count, 1)
        self.assertIsNotNone(touched.last_used_at)

    async def test_touch_missing_is_404(self):
        with self.assertRaises(AppException) as ctx:
            await materials_service.touch_material(self.db, 999)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
