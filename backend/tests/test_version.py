"""版本与升级信息的契约：迁移待执行数、版本比较、未配置更新源时不联网。"""

import unittest
from unittest import mock

from app.config import settings
from app.services import health_service, version_service


class _Revision:
    def __init__(self, revision: str):
        self.revision = revision


class _Script:
    def __init__(self, revisions: list[str]):
        self._revisions = revisions

    def walk_revisions(self, base: str, head: str):
        # 与 alembic 一致：从 head 往 base 走，包含 base
        return [_Revision(item) for item in self._revisions]


class SemverTests(unittest.TestCase):
    def test_parse_versions(self):
        self.assertEqual(version_service.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(version_service.parse_version("1.2"), (1, 2, 0))
        self.assertIsNone(version_service.parse_version("not-a-version"))

    def test_update_available(self):
        self.assertTrue(version_service.is_newer("1.0.0", "v1.1.0"))
        self.assertFalse(version_service.is_newer("1.1.0", "v1.1.0"))
        self.assertIsNone(version_service.is_newer("1.0.0", "release-latest"))


class PendingRevisionTests(unittest.TestCase):
    def test_counts_revisions_until_current(self):
        with mock.patch.object(
            health_service,
            "script_directory",
            return_value=_Script(["new_2", "new_1", "current"]),
        ):
            self.assertEqual(
                version_service.pending_revisions("current", "new_2"),
                ["new_2", "new_1"],
            )

    def test_current_head_has_no_pending(self):
        with mock.patch.object(
            health_service,
            "script_directory",
            return_value=_Script(["head"]),
        ):
            self.assertEqual(version_service.pending_revisions("head", "head"), [])

    def test_unknown_current_returns_none(self):
        self.assertIsNone(version_service.pending_revisions(None, "head"))


class VersionInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_shape_and_update_available(self):
        latest = {
            "configured": True,
            "checked": True,
            "tag": "v1.1.0",
            "name": "1.1.0",
            "url": "https://example.com/release",
            "published_at": "2026-10-30T00:00:00Z",
            "checked_at": 1,
            "error": None,
        }
        with (
            mock.patch.object(
                health_service, "read_db_revision", mock.AsyncMock(return_value="old")
            ),
            mock.patch.object(health_service, "code_head_cached", return_value="new"),
            mock.patch.object(
                version_service,
                "pending_revisions",
                return_value=["new", "middle"],
            ),
            mock.patch.object(
                version_service, "_latest_release", mock.AsyncMock(return_value=latest)
            ),
        ):
            data = await version_service.get_version_info(mock.MagicMock())

        self.assertEqual(data["version"], version_service.APP_VERSION)
        self.assertEqual(data["migration"]["current"], "old")
        self.assertEqual(data["migration"]["head"], "new")
        self.assertEqual(data["migration"]["pending"], 2)
        self.assertFalse(data["migration"]["in_sync"])
        self.assertTrue(data["update_available"])
        self.assertIn("备份", data["backup_hint"])

    async def test_no_repo_means_no_network(self):
        with mock.patch.object(settings, "APP_UPDATE_REPO", ""):
            data = await version_service._latest_release()
        self.assertFalse(data["configured"])
        self.assertFalse(data["checked"])
        self.assertIsNone(data["error"])


if __name__ == "__main__":
    unittest.main()
