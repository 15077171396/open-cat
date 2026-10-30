"""顶栏待办聚合的契约：计数口径、权限裁剪、外部状态（定时任务 / AI 额度）降级。

待办中心最危险的失败方式不是报错，而是「数字不对但看起来正常」：
- 把已处理的记录也算进来 → 管理员永远清不完；
- 没权限的类别也显示 → 点进去 403；
- 定时任务 / Redis 读不到就整块 500 → 顶栏跟着崩。

权限过滤与计数在服务端完成，前端只负责呈现，所以这里全部在服务层验证。
"""

import unittest
from unittest import mock

from app.models.auth_request import AuthRequest
from app.models.feedback import Feedback
from app.models.moderation import ContentModerationLog, ContentReport
from app.models.user import User
from app.services import admin_todo_service, ai_usage_service, rbac_service, system_status_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def _admin(uid: int = 1) -> User:
    return User(id=uid, account=f"admin{uid}@example.com", password_hash="x", role="admin")


def _seed_all(db: MemorySession) -> None:
    db.seed(
        AuthRequest(id=1, user_id=10, star_code="s1", status="pending", pending_key="pending"),
        AuthRequest(id=2, user_id=11, star_code="s2", status="pending", pending_key="pending"),
        AuthRequest(id=3, user_id=12, star_code="s3", status="approved", pending_key=None),
        Feedback(id=4, user_id=10, content="反馈 A", status="pending"),
        Feedback(id=5, user_id=11, content="反馈 B", status="resolved"),
        ContentReport(
            id=6, reporter_id=10, content_type="comment", target_id="1", reason="spam",
            status="pending",
        ),
        ContentReport(
            id=7, reporter_id=11, content_type="comment", target_id="2", reason="abuse",
            status="resolved",
        ),
        ContentModerationLog(
            id=8, content_type="nickname", target_id="10", action="reject", status="pending",
        ),
        ContentModerationLog(
            id=9, content_type="nickname", target_id="11", action="reject", status="approved",
        ),
    )


class AdminTodoServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        self.user = _admin()

    def _rbac(self, perms: list[str], *, super_admin: bool = False):
        roles = [{"id": 1, "code": "super_admin", "name": "超管"}] if super_admin else []

        async def _fake(_db, _user_id):
            return {"roles": roles, "perms": perms}

        return mock.patch.object(rbac_service, "get_user_rbac", _fake)

    def _jobs(self, jobs: list[dict]):
        return mock.patch.object(
            system_status_service, "get_jobs_status", mock.AsyncMock(return_value=jobs)
        )

    def _quota(self, pressure: dict | None):
        return mock.patch.object(
            ai_usage_service, "get_quota_pressure", mock.AsyncMock(return_value=pressure)
        )

    async def _load(self, perms: list[str], *, super_admin: bool = False, jobs=None, quota=None):
        with self._rbac(perms, super_admin=super_admin), self._jobs(jobs or []), self._quota(quota):
            return await admin_todo_service.get_admin_todos(self.db, self.user)

    async def test_counts_only_pending_records(self):
        _seed_all(self.db)
        data = await self._load(
            ["auth_request:view", "feedback:view", "moderation:view", "report:view"]
        )
        self.assertEqual(
            [(g["key"], g["count"]) for g in data["groups"]],
            [("auth_request", 2), ("report", 1), ("moderation", 1), ("feedback", 1)],
        )
        self.assertEqual(data["total"], 5, "已处理 / 已通过的记录不得计入待办")

    async def test_groups_without_permission_are_hidden(self):
        _seed_all(self.db)
        data = await self._load(["feedback:view"])
        self.assertEqual([g["key"] for g in data["groups"]], ["feedback"])
        self.assertEqual(data["total"], 1)

    async def test_super_admin_sees_every_group(self):
        _seed_all(self.db)
        data = await self._load([], super_admin=True)
        self.assertEqual(
            {g["key"] for g in data["groups"]},
            {"auth_request", "report", "moderation", "feedback"},
        )

    async def test_empty_database_returns_empty_list(self):
        data = await self._load(
            ["auth_request:view", "feedback:view", "moderation:view", "report:view"]
        )
        self.assertEqual(data, {"total": 0, "groups": []})

    async def test_failed_job_and_quota_pressure_are_reported(self):
        jobs = [
            {"id": "backup", "label": "数据库备份", "enabled": True, "ok": False},
            {"id": "cleanup", "label": "孤儿清理", "enabled": True, "ok": True},
            {"id": "disabled", "label": "已停用任务", "enabled": False, "ok": False},
        ]
        pressure = {
            "dimension": "daily",
            "label": "今日 AI 调用",
            "used": 95,
            "limit": 100,
            "remaining": 5,
            "ratio": 0.95,
        }
        data = await self._load(["dashboard:view"], jobs=jobs, quota=pressure)

        by_key = {g["key"]: g for g in data["groups"]}
        self.assertEqual(by_key["scheduled_job"]["count"], 1, "停用的任务不应算失败")
        self.assertEqual(by_key["scheduled_job"]["severity"], "error")
        self.assertEqual(by_key["scheduled_job"]["path"], "/scheduled-jobs")
        self.assertIn("数据库备份", by_key["scheduled_job"]["detail"])
        self.assertEqual(by_key["ai_quota"]["count"], 1)
        self.assertIn("95/100", by_key["ai_quota"]["detail"])
        self.assertEqual(data["total"], 2)

    async def test_no_job_failure_and_no_quota_means_no_groups(self):
        data = await self._load(
            ["dashboard:view"],
            jobs=[{"id": "cleanup", "label": "孤儿清理", "enabled": True, "ok": True}],
            quota=None,
        )
        self.assertEqual(data, {"total": 0, "groups": []})

    async def test_job_or_quota_reader_failure_does_not_break_other_groups(self):
        """定时任务读不到时返回空表，实名 / 反馈等数据库计数照常可用。"""
        _seed_all(self.db)
        with (
            self._rbac(["auth_request:view", "dashboard:view"]),
            self._jobs([]),
            self._quota(None),
        ):
            data = await admin_todo_service.get_admin_todos(self.db, self.user)
        self.assertEqual([g["key"] for g in data["groups"]], ["auth_request"])


class QuotaPressureTests(unittest.IsolatedAsyncioTestCase):
    """额度提醒的触发线：只在达到上限 90% 时提醒，且挑最紧张的一维。"""

    def setUp(self):
        self.redis = FakeRedis()
        self._patch_redis = mock.patch.object(ai_usage_service, "redis_client", self.redis)
        self._patch_redis.start()
        self.addCleanup(self._patch_redis.stop)

    def _config(self, **kwargs):
        config = ai_usage_service.QuotaConfig(**kwargs)
        return mock.patch.object(
            ai_usage_service, "get_quota_config", mock.AsyncMock(return_value=config)
        )

    async def _set_calls(self, today: int = 0, month: int = 0, images: int = 0):
        now = ai_usage_service.datetime.now(ai_usage_service.timezone.utc)
        for key, value in (
            (ai_usage_service._calls_day_key(now), today),
            (ai_usage_service._calls_month_key(now), month),
            (ai_usage_service._images_day_key(now), images),
        ):
            if value:
                await self.redis.set(key, str(value))

    async def test_returns_none_when_no_limit_configured(self):
        with self._config():
            self.assertIsNone(await ai_usage_service.get_quota_pressure())

    async def test_returns_none_below_threshold(self):
        await self._set_calls(today=89)
        with self._config(daily_limit=100):
            self.assertIsNone(await ai_usage_service.get_quota_pressure())

    async def test_reports_daily_usage_at_threshold(self):
        await self._set_calls(today=90)
        with self._config(daily_limit=100):
            pressure = await ai_usage_service.get_quota_pressure()
        self.assertEqual(pressure["dimension"], "daily")
        self.assertEqual(pressure["used"], 90)
        self.assertEqual(pressure["remaining"], 10)
        self.assertEqual(pressure["label"], "今日 AI 调用")

    async def test_picks_the_most_utilized_dimension(self):
        await self._set_calls(today=50, month=9900, images=10)
        with self._config(daily_limit=100, monthly_limit=10000, image_daily_limit=100):
            pressure = await ai_usage_service.get_quota_pressure()
        self.assertEqual(pressure["dimension"], "monthly")
        self.assertEqual(pressure["remaining"], 100)

    async def test_image_quota_is_reported(self):
        await self._set_calls(images=95)
        with self._config(image_daily_limit=100):
            pressure = await ai_usage_service.get_quota_pressure()
        self.assertEqual(pressure["dimension"], "images")


if __name__ == "__main__":
    unittest.main()
