"""图片资产模型与迁移契约：表名、关键列、唯一索引。"""

import unittest

from app.models.image_asset import ImageAsset


class ImageAssetModelTests(unittest.TestCase):
    def test_table_and_columns(self):
        self.assertEqual(ImageAsset.__tablename__, "image_assets")
        columns = {c.name for c in ImageAsset.__table__.columns}
        expected = {
            "id", "source", "source_id", "sha256", "title", "alt", "author",
            "author_url", "page_url", "license", "license_url",
            "requires_attribution", "width", "height", "size_bytes",
            "cos_url", "thumb_url", "query", "tags", "created_by",
            "kind", "mime_type", "filename", "usage_count", "last_used_at",
            "is_active", "created_at", "updated_at",
        }
        self.assertTrue(expected.issubset(columns), columns)

    def test_source_id_is_unique_per_source(self):
        from sqlalchemy import UniqueConstraint

        unique = [
            c for c in ImageAsset.__table__.constraints
            if isinstance(c, UniqueConstraint)
            and [col.name for col in c.columns] == ["source", "source_id"]
        ]
        self.assertEqual(len(unique), 1, "缺少 (source, source_id) 唯一约束")

    def test_sha256_is_indexed(self):
        indexes = {idx.name: idx for idx in ImageAsset.__table__.indexes}
        self.assertTrue(any(
            [c.name for c in idx.columns] == ["sha256"] for idx in indexes.values()
        ))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
