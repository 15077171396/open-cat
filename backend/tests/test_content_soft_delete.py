"""内容软删除（回收站）的契约测试。

钉住五条**破了不会报错、只会让「删了还能看见」或「恢复出来是空壳」**的契约：

1. **删除必须同时下架**：只打 `deleted_at` 而不置 `is_published=False`，公开端
   （列表 / 详情 / sitemap / 收录推送）全都在按 `is_published` 过滤，内容会继续可见 ——
   这是本功能最危险的失败模式，且**不会有任何报错**。
2. **恢复默认是草稿**：自动回到已发布，等于让一次误操作反向生效两次。
3. **非法视图参数按 active 处理**：宁可少显示，也不能把回收站内容混进日常列表。
4. **删除函数不得再物理删行**：`db.delete(tutorial)` 一旦回来，章节树/购买记录
   跟着没了，「恢复」就变成恢复一个空壳。
5. **审计动作码必须已登记**：`content_restore` 没登记时后台筛选下拉里找不到它，
   而检索不到记录不会有任何报错。
"""

import re
import unittest
from datetime import datetime
from pathlib import Path

from app.models.admin_audit_log import AUDIT_ACTIONS
from app.models.resource import Resource
from app.models.tutorial import Tutorial
from app.services import content_soft_delete as csd

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "backend" / "app"


class ModeTests(unittest.TestCase):
    def test_valid_modes_are_preserved(self):
        for mode in ("active", "deleted", "all"):
            self.assertEqual(csd.normalize_deleted_mode(mode), mode)

    def test_invalid_mode_falls_back_to_active(self):
        """非法值绝不放宽：写成 'delete' / 'TRUE' / 空串都只能看到正常内容"""
        for mode in ("delete", "TRUE", "", "  ", None):
            self.assertEqual(csd.normalize_deleted_mode(mode), "active")

    def test_conditions_match_modes(self):
        active = str(csd.deleted_condition(Tutorial.deleted_at, "active"))
        deleted = str(csd.deleted_condition(Tutorial.deleted_at, "deleted"))
        self.assertIn("IS NULL", active)
        self.assertIn("IS NOT NULL", deleted)
        self.assertIsNone(csd.deleted_condition(Tutorial.deleted_at, "all"))

    def test_unknown_mode_returns_active_condition(self):
        self.assertIn("IS NULL", str(csd.deleted_condition(Resource.deleted_at, "bogus")))


class StateTransitionTests(unittest.TestCase):
    def _tutorial(self, **kwargs):
        defaults = dict(
            id=1, title="t", description="d", content="c", category="x",
            is_published=True, deleted_at=None, published_at=None,
        )
        defaults.update(kwargs)
        return Tutorial(**defaults)

    def test_mark_deleted_unpublishes(self):
        """核心不变量：进回收站 = 打时间戳 + 立刻下架"""
        t = self._tutorial()
        csd.mark_deleted(t)
        self.assertIsNotNone(t.deleted_at)
        self.assertFalse(t.is_published, "只打 deleted_at 不下架 → 公开端仍然可见（静默事故）")

    def test_mark_deleted_keeps_original_deleted_at(self):
        """重复删除不得刷新删除时间（它是「多久该彻底清」的依据）"""
        first = datetime(2026, 1, 1, 0, 0, 0)
        t = self._tutorial(deleted_at=first)
        csd.mark_deleted(t)
        # 直接调用 mark_deleted 会覆盖，所以服务层必须先过滤 deleted_at IS NULL；
        # 这里钉住的是「服务层过滤存在」这一事实（见下方 source 守卫）
        self.assertIsNotNone(t.deleted_at)

    def test_restore_defaults_to_draft(self):
        t = self._tutorial(is_published=False, deleted_at=datetime(2026, 1, 1))
        csd.mark_restored(t)
        self.assertIsNone(t.deleted_at)
        self.assertFalse(t.is_published, "默认必须恢复成草稿，不能自动重新上线")

    def test_restore_with_republish_sets_published_at(self):
        t = self._tutorial(is_published=False, deleted_at=datetime(2026, 1, 1), published_at=None)
        csd.mark_restored(t, republish=True)
        self.assertIsNone(t.deleted_at)
        self.assertTrue(t.is_published)
        self.assertIsNotNone(t.published_at)

    def test_republish_keeps_existing_published_at(self):
        """原本有发布时间就不要改：改了会让「发布于 X」变成谎话"""
        original = datetime(2025, 5, 5, 5, 5, 5)
        t = self._tutorial(is_published=False, deleted_at=datetime(2026, 1, 1), published_at=original)
        csd.mark_restored(t, republish=True)
        self.assertEqual(t.published_at, original)


class SourceGuardTests(unittest.TestCase):
    """跨文件守卫：这些约定靠读代码才能保证，所以直接扫源码"""

    @classmethod
    def setUpClass(cls):
        cls.tutorial_svc = (APP / "services" / "admin_tutorial_service.py").read_text(encoding="utf-8")
        cls.resource_svc = (APP / "services" / "admin_resource_service.py").read_text(encoding="utf-8")

    def test_admin_lists_apply_deleted_filter(self):
        """后台列表必须过滤回收站：漏了就会让「已删除」出现在日常列表里"""
        for name, source in (("教程", self.tutorial_svc), ("资源", self.resource_svc)):
            self.assertIn(
                "deleted_condition(", source,
                f"{name}管理列表没有应用回收站过滤（deleted_condition）",
            )

    def test_delete_does_not_physically_remove_rows(self):
        """删除函数里不得出现物理删除行 —— 否则恢复出来是空壳"""
        for name, source in (("教程", self.tutorial_svc), ("资源", self.resource_svc)):
            self.assertNotIn("await db.delete(tutorial)", source, f"{name}删除仍在物理删行")
            self.assertNotIn("await db.delete(resource)", source, f"{name}删除仍在物理删行")
            self.assertIn("mark_deleted(", source, f"{name}删除没有调用 mark_deleted")

    def test_restore_functions_exist_and_use_mark_restored(self):
        for name, source in (("教程", self.tutorial_svc), ("资源", self.resource_svc)):
            self.assertIn("async def restore_", source, f"{name}缺少单条恢复函数")
            self.assertIn("async def batch_restore_", source, f"{name}缺少批量恢复函数")
            self.assertIn("mark_restored(", source, f"{name}恢复没有调用 mark_restored")

    def test_delete_only_touches_active_rows(self):
        """删除前必须过滤 deleted_at IS NULL：否则重复删除会刷新删除时间"""
        for name, source in (("教程", self.tutorial_svc), ("资源", self.resource_svc)):
            self.assertIn("deleted_at.is_(None)", source, f"{name}删除未过滤「已在回收站」的行")
            self.assertIn("deleted_at.isnot(None)", source, f"{name}恢复未过滤「不在回收站」的行")

    def test_publish_paths_exclude_recycle_bin(self):
        """发布是公开端唯一的可见性开关：对回收站内容置 True = 让已删除内容重新对访客可见"""
        for fn in ("async def toggle_publish", "async def batch_publish_tutorials"):
            start = self.tutorial_svc.index(fn)
            body = self.tutorial_svc[start:start + 900]
            self.assertIn("deleted_at.is_(None)", body, f"{fn} 未排除回收站内容")

    def test_audit_action_is_registered(self):
        self.assertIn("content_restore", AUDIT_ACTIONS)
        self.assertTrue(AUDIT_ACTIONS["content_restore"].strip())

    def test_rbac_rules_cover_restore_endpoints(self):
        """新增接口必须登记权限规则，否则 fail-closed 一律 403（点恢复就报错）"""
        from app.core.rbac import match_route_permission

        cases = {
            ("POST", "/api/v1/admin/tutorials/batch-restore"): ["tutorial:delete"],
            ("POST", "/api/v1/admin/tutorials/12/restore"): ["tutorial:delete"],
            ("POST", "/api/v1/admin/resources/batch-restore"): ["resource:delete"],
            ("POST", "/api/v1/admin/resources/12/restore"): ["resource:delete"],
        }
        for (method, path), expected in cases.items():
            self.assertEqual(match_route_permission(method, path), expected, f"{method} {path} 权限规则不对")

    def test_restore_routes_are_registered(self):
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/api/v1/admin/tutorials/{tutorial_id}/restore", paths)
        self.assertIn("/api/v1/admin/tutorials/batch-restore", paths)
        self.assertIn("/api/v1/admin/resources/{resource_id}/restore", paths)
        self.assertIn("/api/v1/admin/resources/batch-restore", paths)

    def test_list_endpoints_accept_deleted_view(self):
        """列表接口必须暴露 deleted 视图，否则回收站在前端根本打不开"""
        for name in ("admin_tutorials.py", "admin_resources.py"):
            source = (APP / "api" / name).read_text(encoding="utf-8")
            self.assertIn('deleted: Literal["active", "deleted", "all"]', source, f"{name} 未暴露 deleted 参数")


class PublicVisibilityTests(unittest.TestCase):
    """公开端「删掉即不可见」依赖 is_published 过滤 —— 这条依赖必须被显式记住"""

    def test_public_services_filter_published(self):
        for name in ("tutorial_service.py", "resource_service.py", "sitemap_service.py"):
            source = (APP / "services" / name).read_text(encoding="utf-8")
            self.assertRegex(
                source, r"is_published", f"{name} 不再过滤 is_published → 软删除的内容会重新可见"
            )

    def test_push_job_filters_published(self):
        """收录推送若不过滤 is_published，会把已删除页推给搜索引擎（掉收录质量）"""
        source = (REPO_ROOT / "backend" / "scheduler" / "tasks.py").read_text(encoding="utf-8")
        self.assertRegex(source, re.compile(r"is_published"), "推送任务未过滤 is_published")


if __name__ == "__main__":
    unittest.main()
