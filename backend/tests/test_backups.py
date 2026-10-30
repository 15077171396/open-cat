"""备份管理的安全契约：路径不能逃逸、下载必须留审计、目录不可见不报 500。"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.config import settings
from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User
from app.services import backup_service
from tests.fake_db import MemorySession


def _admin() -> User:
    return User(id=1, account="admin@test", password_hash="x", role="admin")


class BackupListTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.patch = mock.patch.object(settings, "BACKUP_DIR", str(self.dir))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def _write(self, name: str, content: bytes, age_seconds: int = 0) -> Path:
        path = self.dir / name
        path.write_bytes(content)
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    async def test_lists_newest_first_with_metadata(self):
        self._write("ai_cat_20261030_030000.sql.gz", b"a" * 100, age_seconds=3600)
        self._write("pre_restore_20261030_040000.sql.gz", b"b" * 200, age_seconds=60)
        (self.dir / "ai_cat_20261030_030000.sql.gz.sha256").write_text("x", encoding="utf-8")
        (self.dir / "backup.log").write_text("noise", encoding="utf-8")

        data = await backup_service.list_backups(can_download=True)

        self.assertTrue(data["visible"])
        self.assertEqual(data["count"], 2, "只列 .sql.gz，不把日志/校验文件当备份")
        self.assertEqual(data["latest"], "pre_restore_20261030_040000.sql.gz")
        self.assertEqual(data["items"][0]["kind"], "pre_restore")
        self.assertEqual(data["items"][1]["kind"], "auto")
        self.assertEqual(data["items"][1]["has_checksum"], True)
        self.assertTrue(data["can_download"])
        self.assertEqual(data["commands"]["install_cron"], "./scripts/backup_mysql.sh --install-cron")

    async def test_missing_dir_is_visible_false_not_error(self):
        with mock.patch.object(settings, "BACKUP_DIR", str(self.dir / "missing")):
            data = await backup_service.list_backups()
        self.assertFalse(data["visible"])
        self.assertEqual(data["items"], [])
        self.assertTrue(data["stale"])

    async def test_download_rejects_traversal_and_non_backup_names(self):
        for bad in ("../etc/passwd.sql.gz", "a/b.sql.gz", "notes.txt"):
            with self.subTest(name=bad), self.assertRaises(AppException) as ctx:
                await backup_service.prepare_download(MemorySession(), _admin(), bad)
            self.assertEqual(ctx.exception.code, 400)

    async def test_download_writes_audit_before_returning_path(self):
        target = self._write("ai_cat_20261030_050000.sql.gz", b"backup-data")
        db = MemorySession()
        path = await backup_service.prepare_download(
            db, _admin(), "ai_cat_20261030_050000.sql.gz"
        )
        self.assertEqual(path, target.resolve())
        rows = db.all_rows(AdminAuditLog)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "data_export")
        self.assertEqual(rows[0]["target_type"], "backup")
        self.assertIn("ai_cat_20261030_050000.sql.gz", rows[0]["summary"])
        self.assertEqual(db.commits, 1)


if __name__ == "__main__":
    unittest.main()
