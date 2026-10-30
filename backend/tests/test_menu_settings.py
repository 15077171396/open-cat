"""菜单管理（展示层覆盖）回归。

边界必须钉死：
- 只改名称/图标/可见/排序；未知 key、超长名称要拒绝。
- 覆盖存 system_settings.menu_overrides，`menu_tree` 读取后立即生效。
- 恢复单项/全部 = 回到代码默认。
- 每个写操作留审计。
"""

from __future__ import annotations

import asyncio
import unittest

from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User
from app.services import admin_menu, menu_setting_service
from tests.fake_db import MemorySession


def _admin() -> User:
    return User(id=1, account="admin@test", role="admin")


def _run(coro):
    return asyncio.run(coro)


def _overview(tree: dict) -> dict:
    return next(group for group in tree["groups"] if group["key"] == "overview")


class MenuSettingsTests(unittest.TestCase):
    def test_default_tree_contains_code_catalog(self):
        db = MemorySession()
        data = _run(menu_setting_service.settings_tree(db))
        self.assertGreaterEqual(len(data["groups"]), 5)
        self.assertEqual(data["custom_count"], 0)
        system = next(group for group in data["groups"] if group["key"] == "system")
        keys = {item["key"] for item in system["items"]}
        self.assertIn("/menu-settings", keys)

    def test_update_label_icon_and_menu_tree_applies_it(self):
        db = MemorySession()
        admin = _admin()
        _run(
            menu_setting_service.update_override(
                db,
                admin,
                "/",
                {"label_zh": "首页", "label_en": "Homepage", "icon": "home"},
            )
        )
        overrides = _run(menu_setting_service.load_overrides(db))
        tree = admin_menu.menu_tree(locale="zh-CN", overrides=overrides)
        dashboard = next(item for item in _overview(tree)["items"] if item["key"] == "/")
        self.assertEqual(dashboard["label"], "首页")
        self.assertEqual(dashboard["icon"], "home")

        tree_en = admin_menu.menu_tree(locale="en", overrides=overrides)
        dashboard_en = next(item for item in _overview(tree_en)["items"] if item["key"] == "/")
        self.assertEqual(dashboard_en["label"], "Homepage")

    def test_hide_item_and_group(self):
        db = MemorySession()
        admin = _admin()
        _run(menu_setting_service.update_override(db, admin, "/analysis", {"visible": False}))
        tree = admin_menu.menu_tree(overrides=_run(menu_setting_service.load_overrides(db)))
        self.assertNotIn("/analysis", {item["key"] for item in _overview(tree)["items"]})

        _run(menu_setting_service.update_override(db, admin, "overview", {"visible": False}))
        tree = admin_menu.menu_tree(overrides=_run(menu_setting_service.load_overrides(db)))
        self.assertNotIn("overview", {group["key"] for group in tree["groups"]})

    def test_reorder_items(self):
        db = MemorySession()
        admin = _admin()
        _run(
            menu_setting_service.reorder(
                db,
                admin,
                [
                    {"key": "/analysis", "sort_order": 0},
                    {"key": "/", "sort_order": 1},
                ],
            )
        )
        tree = admin_menu.menu_tree(overrides=_run(menu_setting_service.load_overrides(db)))
        keys = [item["key"] for item in _overview(tree)["items"]]
        self.assertEqual(keys[:2], ["/analysis", "/"])

    def test_reset_one_and_all(self):
        db = MemorySession()
        admin = _admin()
        _run(menu_setting_service.update_override(db, admin, "/", {"label_zh": "首页"}))
        _run(menu_setting_service.update_override(db, admin, "/analysis", {"label_zh": "分析"}))
        self.assertEqual(_run(menu_setting_service.settings_tree(db))["custom_count"], 2)

        _run(menu_setting_service.reset_one(db, admin, "/"))
        self.assertEqual(_run(menu_setting_service.settings_tree(db))["custom_count"], 1)

        removed = _run(menu_setting_service.reset_all(db, admin))
        self.assertEqual(removed, 1)
        self.assertEqual(_run(menu_setting_service.settings_tree(db))["custom_count"], 0)
        tree = admin_menu.menu_tree(overrides=_run(menu_setting_service.load_overrides(db)))
        dashboard = next(item for item in _overview(tree)["items"] if item["key"] == "/")
        self.assertEqual(dashboard["label"], "数据看板")

    def test_unknown_key_and_long_label_are_rejected(self):
        db = MemorySession()
        admin = _admin()
        with self.assertRaises(AppException) as ctx:
            _run(menu_setting_service.update_override(db, admin, "/not-exist", {"label_zh": "x"}))
        self.assertEqual(ctx.exception.code, 404)

        with self.assertRaises(AppException) as ctx:
            _run(menu_setting_service.update_override(db, admin, "/", {"label_zh": "x" * 65}))
        self.assertEqual(ctx.exception.code, 400)

    def test_every_write_is_audited(self):
        db = MemorySession()
        admin = _admin()
        _run(menu_setting_service.update_override(db, admin, "/", {"label_zh": "首页"}))
        _run(menu_setting_service.reorder(db, admin, [{"key": "/", "sort_order": 3}]))
        _run(menu_setting_service.reset_one(db, admin, "/"))
        _run(menu_setting_service.reset_all(db, admin))

        actions = [row["action"] for row in db.all_rows(AdminAuditLog)]
        self.assertIn("menu_setting_update", actions)
        self.assertIn("menu_setting_reset", actions)


if __name__ == "__main__":
    unittest.main()
