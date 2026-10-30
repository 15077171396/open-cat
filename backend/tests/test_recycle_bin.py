"""统一回收站的契约：权限裁剪、保留期口径、批量恢复与到期清理。

最容易静默出错的四件事：
1. 没权限的类型出现在汇总里（点进去 403）；
2. 把「有购买记录的教程」当成普通内容清掉（用户已付费的权益消失）；
3. 恢复顶层评论时把「后来单独删掉」的回复也一起放回来；
4. 批次恢复只恢复了一部分，却显示成功。
"""

import unittest
from datetime import datetime, timedelta
from unittest import mock

from app.models.admin_audit_log import AdminAuditLog
from app.models.comment import ContentComment
from app.models.tutorial import Tutorial, TutorialNetdiskLink, TutorialPurchase
from app.models.tutorial_node import TutorialNode
from app.models.user import User
from app.services import (
    admin_user_service,
    comment_service,
    rbac_service,
    recycle_bin_service,
)
from tests.fake_db import MemorySession


def _admin() -> User:
    return User(id=1, account="admin@test", password_hash="x", role="admin")


def _user(uid: int, deleted_days_ago: int | None = None) -> User:
    return User(
        id=uid,
        account=f"user{uid}@test",
        password_hash="x",
        role="user",
        deleted_at=(
            datetime(2026, 10, 30) - timedelta(days=deleted_days_ago)
            if deleted_days_ago is not None
            else None
        ),
    )


def _tutorial(tid: int, deleted_days_ago: int | None) -> Tutorial:
    return Tutorial(
        id=tid,
        title=f"教程 {tid}",
        description="简介",
        category="AI",
        content="正文",
        deleted_at=(
            datetime(2026, 10, 30) - timedelta(days=deleted_days_ago)
            if deleted_days_ago is not None
            else None
        ),
    )


def _comment(cid: int, *, content: str = "评论", parent_id: int | None = None,
             deleted_days_ago: int | None = None, target_id: int = 1) -> ContentComment:
    return ContentComment(
        id=cid,
        target_type="tutorial",
        target_id=target_id,
        user_id=2,
        parent_id=parent_id,
        content=content,
        deleted_at=(
            datetime(2026, 10, 30) - timedelta(days=deleted_days_ago)
            if deleted_days_ago is not None
            else None
        ),
    )


class _PermissionMixin:
    def _rbac(self, perms: list[str], *, super_admin: bool = False):
        roles = [{"id": 1, "code": "super_admin", "name": "超管"}] if super_admin else []

        async def _fake(_db, _user_id):
            return {"roles": roles, "perms": perms}

        return mock.patch.object(rbac_service, "get_user_rbac", _fake)


class SummaryTests(_PermissionMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        self.db.seed(
            _tutorial(1, 1),
            _comment(1, deleted_days_ago=1),
            _user(1, deleted_days_ago=1),
        )

    async def test_only_visible_types_are_returned(self):
        with self._rbac(["tutorial:view"]):
            data = await recycle_bin_service.get_summary(self.db, _admin())
        self.assertEqual([item["type"] for item in data["items"]], ["tutorial"])
        self.assertEqual(data["items"][0]["count"], 1)
        self.assertEqual(data["items"][0]["retention_days"], 90)
        self.assertFalse(data["items"][0]["can_restore"], "没有 tutorial:delete 不应点亮恢复")

    async def test_super_admin_sees_all_and_can_restore(self):
        with self._rbac([], super_admin=True):
            data = await recycle_bin_service.get_summary(self.db, _admin())
        self.assertEqual(
            [(item["type"], item["retention_days"], item["can_restore"]) for item in data["items"]],
            [("tutorial", 90, True), ("comment", 30, True), ("user", None, True)],
        )
        self.assertEqual(data["total"], 3)


class ListTests(_PermissionMixin, unittest.IsolatedAsyncioTestCase):
    async def test_tutorial_list_locks_purchased_items(self):
        db = MemorySession()
        db.seed(
            _tutorial(1, 1),
            _tutorial(2, 2),
            _tutorial(3, None),
            TutorialPurchase(id=1, user_id=9, tutorial_id=2, points_paid=10),
        )
        with self._rbac(["tutorial:view"], super_admin=True):
            data = await recycle_bin_service.list_tutorials(db, _admin(), 1, 20)
        self.assertEqual(data["total"], 2)
        by_id = {item["id"]: item for item in data["items"]}
        self.assertEqual(by_id[1]["remaining_days"], 89)
        self.assertIsNone(by_id[2]["retention_days"], "有购买记录的教程不能显示倒计时")
        self.assertIn("购买", by_id[2]["retention_note"])

    async def test_comment_list_maps_content_and_author(self):
        db = MemorySession()
        with (
            self._rbac(["comment:view", "comment:delete"]),
            mock.patch.object(
                comment_service,
                "admin_list_comments",
                mock.AsyncMock(return_value={
                    "items": [{
                        "id": 7,
                        "content": "违规内容",
                        "target_type": "tutorial",
                        "target_id": 1,
                        "target_title": "教程 A",
                        "parent_id": None,
                        "author_nickname": "小明",
                        "deleted_at": "2026-10-29T00:00:00",
                    }],
                    "total": 1,
                    "page": 1,
                    "page_size": 20,
                }),
            ),
        ):
            data = await recycle_bin_service.list_comments(db, _admin(), 1, 20)
        item = data["items"][0]
        self.assertEqual(item["title"], "违规内容")
        self.assertIn("小明", item["subtitle"])
        self.assertIn("教程 A", item["subtitle"])
        self.assertEqual(item["retention_days"], 30)
        self.assertTrue(item["can_restore"])

    async def test_user_list_has_no_auto_purge(self):
        db = MemorySession()
        with (
            self._rbac(["user:view"]),
            mock.patch.object(
                admin_user_service,
                "list_users",
                mock.AsyncMock(return_value={
                    "items": [{
                        "id": 5,
                        "account": "gone@test",
                        "role": "user",
                        "department_name": None,
                        "deleted_at": "2026-10-01T00:00:00",
                    }],
                    "total": 1,
                    "page": 1,
                    "page_size": 20,
                }),
            ),
        ):
            data = await recycle_bin_service.list_users(db, _admin(), 1, 20)
        item = data["items"][0]
        self.assertIsNone(item["retention_days"])
        self.assertIsNone(item["remaining_days"])
        self.assertEqual(item["retention_note"], "账号不自动清理")


class RestoreTests(_PermissionMixin, unittest.IsolatedAsyncioTestCase):
    async def test_restore_tutorial_uses_existing_batch_service(self):
        db = MemorySession()
        db.seed(_tutorial(1, 3))
        with (
            self._rbac([], super_admin=True),
            mock.patch.object(
                recycle_bin_service, "invalidate_tutorials", mock.AsyncMock()
            ),
        ):
            result = await recycle_bin_service.restore_tutorials(db, [1], _admin())
        self.assertEqual(result["restored"], 1)
        self.assertIsNone(db.all_rows(Tutorial)[0]["deleted_at"])
        self.assertEqual(db.all_rows(AdminAuditLog)[0]["action"], "content_restore")

    async def test_restoring_parent_only_restores_replies_deleted_at_same_time(self):
        db = MemorySession()
        db.seed(
            _comment(1, deleted_days_ago=2),
            _comment(2, parent_id=1, deleted_days_ago=2),  # 与父评论同时被连带删除
            _comment(3, parent_id=1, deleted_days_ago=1),  # 后来单独删除
        )
        result = await recycle_bin_service.restore_comments(db, [1], _admin())
        self.assertEqual(result["restored"], 2, "同一时刻连带删除的回复应一起恢复")
        by_id = {row["id"]: row for row in db.all_rows(ContentComment)}
        self.assertIsNone(by_id[1]["deleted_at"])
        self.assertIsNone(by_id[2]["deleted_at"])
        self.assertIsNotNone(by_id[3]["deleted_at"], "后来单独删除的回复不能被误恢复")
        self.assertEqual(db.all_rows(AdminAuditLog)[0]["action"], "comment_restore")

    async def test_restore_users_only_touches_deleted_rows(self):
        db = MemorySession()
        db.seed(_user(1, 3), _user(2, None))
        result = await recycle_bin_service.restore_users(db, [1, 2], _admin())
        self.assertEqual(result["restored"], 1)
        by_id = {row["id"]: row for row in db.all_rows(User)}
        self.assertIsNone(by_id[1]["deleted_at"])
        self.assertIsNone(by_id[2]["deleted_at"])
        self.assertEqual(db.all_rows(AdminAuditLog)[0]["action"], "user_restore")


class PurgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30)
        self.db = MemorySession()

    async def _run(self):
        result = await recycle_bin_service.purge_expired(self.db, now=self.now)
        await self.db.commit()
        return result

    async def test_purges_old_tutorial_and_comments_but_keeps_guarded_rows(self):
        self.db.seed(
            _tutorial(1, 91),
            _tutorial(2, 10),
            TutorialNode(id=1, tutorial_id=1, title="章", content="正文", sort=0),
            TutorialNetdiskLink(id=1, tutorial_id=1, platform="other", url="https://x"),
            TutorialPurchase(id=1, user_id=9, tutorial_id=3, points_paid=10),
            _tutorial(3, 91),
            _comment(1, deleted_days_ago=31, target_id=1),
            _comment(2, deleted_days_ago=10, target_id=2),
        )
        result = await self._run()
        self.assertEqual(result["tutorials"], 1)
        self.assertEqual(result["comments"], 1)
        self.assertEqual(result["tutorials_skipped"], 1)
        self.assertEqual({row["id"] for row in self.db.all_rows(Tutorial)}, {2, 3})
        self.assertEqual(self.db.all_rows(TutorialNode), [])
        self.assertEqual(self.db.all_rows(TutorialNetdiskLink), [])
        self.assertEqual({row["id"] for row in self.db.all_rows(ContentComment)}, {2})


if __name__ == "__main__":
    unittest.main()
