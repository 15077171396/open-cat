"""上传图片进入本站素材库：记录成功、去重与失败不影响上传。"""

import io
import unittest
from unittest import mock

from PIL import Image
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers, UploadFile

from app.api import uploads
from app.services.image_search import asset_recorder
from tests.conftest import async_client, make_user
from tests.fake_db import MemorySession


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), (0, 128, 255)).save(buffer, "PNG")
    return buffer.getvalue()


class AssetRecorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_and_dedupes(self):
        db = MemorySession()
        data = _png()
        asset = await asset_recorder.record_upload_asset(
            db, url="https://cos.example/a.png", data=data,
            content_type="image/png", filename="cat.png", admin_id=7,
        )
        self.assertIsNotNone(asset)
        self.assertEqual(asset.source, "upload")
        self.assertEqual((asset.width, asset.height), (12, 8))
        self.assertEqual(asset.created_by, 7)
        again = await asset_recorder.record_upload_asset(
            db, url="https://cos.example/b.png", data=data,
            content_type="image/png", filename="cat.png", admin_id=7,
        )
        self.assertEqual(again.id, asset.id)
        rows = list(db._tables["image_assets"].rows.values())
        self.assertEqual(len(rows), 1)

    async def test_file_upload_kind_is_inferred(self):
        db = MemorySession()
        asset = await asset_recorder.record_file_asset(
            db, url="https://cos.example/manual.pdf", data=b"%PDF-1.4" + b" hello",
            content_type="application/pdf", filename="manual.pdf", admin_id=7,
        )
        self.assertIsNotNone(asset)
        self.assertEqual(asset.kind, "document")
        self.assertEqual(asset.mime_type, "application/pdf")
        self.assertEqual(asset.filename, "manual.pdf")

    async def test_db_failure_is_swallowed(self):
        db = mock.Mock()
        db.execute = mock.AsyncMock(side_effect=SQLAlchemyError("db down"))
        db.rollback = mock.AsyncMock()
        asset = await asset_recorder.record_upload_asset(
            db, url="https://cos.example/a.png", data=_png(),
            content_type="image/png", filename="cat.png", admin_id=1,
        )
        self.assertIsNone(asset)


class _ExpiringAdmin:
    """模拟 rollback 后 ORM 属性失效：rollback 之后再读 id 必须失败。"""

    def __init__(self):
        self._id = 4
        self.expired = False

    @property
    def id(self):
        if self.expired:
            raise RuntimeError("admin.id read after rollback")
        return self._id


class _RollbackSession:
    def __init__(self, admin: _ExpiringAdmin):
        self.admin = admin

    async def rollback(self):
        self.admin.expired = True


class UploadApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_captures_admin_id_before_rollback(self):
        """回归：rollback 会 expire ORM 对象，admin.id 必须在 rollback 前取。"""
        admin = _ExpiringAdmin()
        db = _RollbackSession(admin)
        file = UploadFile(
            file=io.BytesIO(_png()), filename="cat.png",
            headers=Headers({"content-type": "image/png"}),
        )
        with mock.patch.object(
            uploads.cos_service, "upload_bytes",
            new=mock.AsyncMock(return_value="https://cos.example/up.png"),
        ), mock.patch.object(
            uploads, "record_upload_asset", new=mock.AsyncMock(),
        ) as recorder:
            res = await uploads.upload_image(file=file, db=db, admin=admin)
        self.assertEqual(res["data"]["url"], "https://cos.example/up.png")
        self.assertEqual(recorder.await_args.kwargs["admin_id"], 4)

    async def test_upload_records_asset(self):
        data = _png()
        with mock.patch.object(
            uploads.cos_service, "upload_bytes",
            new=mock.AsyncMock(return_value="https://cos.example/up.png"),
        ), mock.patch.object(
            uploads, "record_upload_asset", new=mock.AsyncMock(),
        ) as recorder:
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post(
                    "/api/v1/uploads/image",
                    files={"file": ("cat.png", data, "image/png")},
                )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["url"], "https://cos.example/up.png")
        recorder.assert_awaited_once()
        kwargs = recorder.await_args.kwargs
        self.assertEqual(kwargs["filename"], "cat.png")
        self.assertEqual(kwargs["content_type"], "image/png")

    async def test_file_upload_records_asset(self):
        data = b"%PDF-1.4" + b" hello"
        with mock.patch.object(
            uploads.cos_service, "upload_bytes",
            new=mock.AsyncMock(return_value="https://cos.example/manual.pdf"),
        ), mock.patch.object(
            uploads, "record_file_asset", new=mock.AsyncMock(),
        ) as recorder:
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post(
                    "/api/v1/uploads/file",
                    files={"file": ("manual.pdf", data, "application/pdf")},
                )
        self.assertEqual(res.status_code, 200)
        recorder.assert_awaited_once()
        self.assertEqual(recorder.await_args.kwargs["filename"], "manual.pdf")

    async def test_asset_record_failure_does_not_fail_upload(self):
        data = _png()
        with mock.patch.object(
            uploads.cos_service, "upload_bytes",
            new=mock.AsyncMock(return_value="https://cos.example/up.png"),
        ), mock.patch.object(
            uploads, "record_upload_asset",
            new=mock.AsyncMock(side_effect=RuntimeError("db down")),
        ):
            async with async_client(user=make_user(role="admin")) as client:
                res = await client.post(
                    "/api/v1/uploads/image",
                    files={"file": ("cat.png", data, "image/png")},
                )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["url"], "https://cos.example/up.png")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
