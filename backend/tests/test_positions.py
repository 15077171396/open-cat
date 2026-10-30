"""岗位目录服务回归。"""

from __future__ import annotations

import asyncio
import unittest

from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User
from app.services import position_service
from tests.fake_db import MemorySession


def _admin() -> User:
    return User(id=1, account="admin@test", role="admin")


def _run(coro):
    return asyncio.run(coro)


class PositionServiceTests(unittest.TestCase):
    def test_crud_and_duplicate_guards(self):
        db = MemorySession()
        row = _run(
            position_service.create_position(
                db,
                _admin(),
                code="content_operator",
                name="内容运营",
                description="负责内容更新",
                sort_order=1,
                is_enabled=True,
            )
        )
        self.assertTrue(row["id"])
        self.assertEqual(_run(position_service.list_positions(db))["items"][0]["name"], "内容运营")

        with self.assertRaises(AppException) as ctx:
            _run(
                position_service.create_position(
                    db, _admin(), code="content_operator", name="另一个", description=None,
                    sort_order=0, is_enabled=True,
                )
            )
        self.assertEqual(ctx.exception.code, 400)

        updated = _run(
            position_service.update_position(
                db, _admin(), row["id"], {"name": "内容主管", "is_enabled": False}
            )
        )
        self.assertEqual(updated["name"], "内容主管")
        self.assertFalse(updated["is_enabled"])

        _run(position_service.delete_position(db, _admin(), row["id"]))
        self.assertEqual(_run(position_service.list_positions(db))["items"], [])

    def test_writes_are_audited(self):
        db = MemorySession()
        row = _run(
            position_service.create_position(
                db, _admin(), code="editor", name="编辑", description=None,
                sort_order=0, is_enabled=True,
            )
        )
        _run(position_service.update_position(db, _admin(), row["id"], {"sort_order": 2}))
        _run(position_service.delete_position(db, _admin(), row["id"]))
        actions = [item["action"] for item in db.all_rows(AdminAuditLog)]
        self.assertEqual(actions.count("position_create"), 1)
        self.assertEqual(actions.count("position_update"), 1)
        self.assertEqual(actions.count("position_delete"), 1)


if __name__ == "__main__":
    unittest.main()
