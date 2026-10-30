"""字典管理服务回归：类型/字典项约束、默认项唯一、删除保护、审计。"""

from __future__ import annotations

import asyncio
import unittest

from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User
from app.services import dictionary_service
from tests.fake_db import MemorySession


def _admin() -> User:
    return User(id=1, account="admin@test", role="admin")


def _run(coro):
    return asyncio.run(coro)


class DictionaryServiceTests(unittest.TestCase):
    def test_type_crud_and_code_validation(self):
        db = MemorySession()
        row = _run(
            dictionary_service.create_type(
                db, _admin(), code="content_status", name="内容状态", description="教程状态", is_enabled=True
            )
        )
        self.assertTrue(row["id"])
        types = _run(dictionary_service.list_types(db))
        self.assertEqual(types["items"][0]["code"], "content_status")
        self.assertEqual(types["items"][0]["item_count"], 0)

        with self.assertRaises(AppException) as ctx:
            _run(dictionary_service.create_type(db, _admin(), code="Content-Status", name="x", description=None, is_enabled=True))
        self.assertEqual(ctx.exception.code, 400)

        updated = _run(
            dictionary_service.update_type(db, _admin(), row["id"], {"name": "内容发布状态", "is_enabled": False})
        )
        self.assertEqual(updated["name"], "内容发布状态")
        self.assertFalse(updated["is_enabled"])

    def test_item_crud_default_uniqueness_and_duplicate_value(self):
        db = MemorySession()
        type_row = _run(
            dictionary_service.create_type(db, _admin(), code="feedback_status", name="反馈状态", description=None, is_enabled=True)
        )
        first = _run(
            dictionary_service.create_item(
                db, _admin(), type_row["id"], label="待处理", value="pending",
                sort_order=0, is_enabled=True, is_default=True, remark=None,
            )
        )
        second = _run(
            dictionary_service.create_item(
                db, _admin(), type_row["id"], label="已处理", value="resolved",
                sort_order=1, is_enabled=True, is_default=True, remark=None,
            )
        )
        items = _run(dictionary_service.list_items(db, type_row["id"]))
        by_id = {item["id"]: item for item in items}
        self.assertFalse(by_id[first["id"]]["is_default"], "默认项只能有一个")
        self.assertTrue(by_id[second["id"]]["is_default"])

        with self.assertRaises(AppException) as ctx:
            _run(
                dictionary_service.create_item(
                    db, _admin(), type_row["id"], label="重复", value="pending",
                    sort_order=2, is_enabled=True, is_default=False, remark=None,
                )
            )
        self.assertEqual(ctx.exception.code, 400)

        with self.assertRaises(AppException):
            _run(
                dictionary_service.update_item(
                    db, _admin(), type_row["id"], second["id"], {"value": "pending"}
                )
            )

        updated = _run(
            dictionary_service.update_item(
                db, _admin(), type_row["id"], first["id"], {"label": "待跟进", "is_default": True}
            )
        )
        self.assertEqual(updated["label"], "待跟进")
        self.assertTrue(updated["is_default"])

    def test_delete_type_requires_empty_items(self):
        db = MemorySession()
        type_row = _run(
            dictionary_service.create_type(db, _admin(), code="user_status", name="用户状态", description=None, is_enabled=True)
        )
        item = _run(
            dictionary_service.create_item(
                db, _admin(), type_row["id"], label="正常", value="active",
                sort_order=0, is_enabled=True, is_default=True, remark=None,
            )
        )
        with self.assertRaises(AppException) as ctx:
            _run(dictionary_service.delete_type(db, _admin(), type_row["id"]))
        self.assertEqual(ctx.exception.code, 400)

        _run(dictionary_service.delete_item(db, _admin(), type_row["id"], item["id"]))
        _run(dictionary_service.delete_type(db, _admin(), type_row["id"]))
        self.assertEqual(_run(dictionary_service.list_types(db))["items"], [])

    def test_writes_are_audited(self):
        db = MemorySession()
        type_row = _run(
            dictionary_service.create_type(db, _admin(), code="moderation_status", name="机审状态", description=None, is_enabled=True)
        )
        item = _run(
            dictionary_service.create_item(
                db, _admin(), type_row["id"], label="待复核", value="pending",
                sort_order=0, is_enabled=True, is_default=False, remark=None,
            )
        )
        _run(dictionary_service.update_item(db, _admin(), type_row["id"], item["id"], {"is_enabled": False}))
        _run(dictionary_service.delete_item(db, _admin(), type_row["id"], item["id"]))
        _run(dictionary_service.delete_type(db, _admin(), type_row["id"]))
        actions = [row["action"] for row in db.all_rows(AdminAuditLog)]
        for action in ("dict_type_create", "dict_item_create", "dict_item_update", "dict_item_delete", "dict_type_delete"):
            self.assertIn(action, actions)


if __name__ == "__main__":
    unittest.main()
