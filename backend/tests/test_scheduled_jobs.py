"""定时任务目录与状态计算（后台「定时任务」页）的契约测试。

这里钉的不是「函数能跑通」，而是五条**破了不会报错、只会让运维做出错误判断**的契约：

1. **目录 ↔ 调度器一一对应**：`JOB_SPECS` 写了但 `add_job(id=...)` 没注册（或反之），
   后台会永远显示「从未执行」，看起来像任务没跑，实际只是名字写错了。
2. **下次执行时间可离线断言**：`next_run_at` 必须接受外部传入的 `now`。
   读时钟的实现在 02:00 前后会得到不同结果，测试会变成随时会红的定时炸弹。
3. **未知 ≠ 失败**：没有心跳（Redis 挂了 / TTL 过期 / 容器没起）必须是 `never`，
   不能是 `failed` —— 一次调度器重启不该刷出一片假故障。
4. **全局静默是一条独立结论**：8 个任务同时没有心跳几乎只可能是调度器没起，
   页面要给出这一句，而不是让人对着 8 条「从未执行」猜。
5. **保留期文案不许漂移**：页面写「保留 365 天」，代码里改常量时必须一起改，
   否则运维会按错误的天数预估磁盘。
"""

import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from app.services import scheduled_job_service as sjs
from app.services import system_status_service as sss
from scheduler import tasks as scheduler_tasks
from tests.conftest import FakeRedis

REPO_ROOT = Path(__file__).resolve().parents[2]

# 固定的「现在」：北京时间 2026-10-27 01:00（UTC 2026-10-26 17:00）
# 选在凌晨 1 点，四个日报任务（02:00~05:00）都还没跑，方便断言「就是今天」
NOW = datetime(2026, 10, 27, 1, 0, tzinfo=sjs.CST).timestamp()


def _spec(job_id: str) -> sjs.JobSpec:
    return next(spec for spec in sjs.JOB_SPECS if spec.id == job_id)


class CatalogTests(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = [spec.id for spec in sjs.JOB_SPECS]
        self.assertEqual(len(ids), len(set(ids)), f"任务 id 重复: {ids}")

    def test_every_spec_is_registered_in_scheduler(self):
        registered = set(_registered_job_ids())
        declared = {spec.id for spec in sjs.JOB_SPECS}
        self.assertFalse(
            declared - registered,
            f"目录里声明但调度器没注册（后台会永远显示「从未执行」）: {sorted(declared - registered)}",
        )

    def test_every_registered_job_is_in_catalog(self):
        """反向：新增任务没进目录 → 后台完全看不到它（静默失效）"""
        declared = {spec.id for spec in sjs.JOB_SPECS}
        extra = set(_registered_job_ids()) - declared
        self.assertFalse(extra, f"调度器注册了但目录未声明（后台不可见）: {sorted(extra)}")

    def test_每类任务都有可读说明(self):
        """页面上要回答「这是干什么的」，desc 为空会让任务名变成一坨黑话"""
        for spec in sjs.JOB_SPECS:
            with self.subTest(job=spec.id):
                self.assertTrue(spec.desc.strip(), f"{spec.id} 缺 desc")
                self.assertIn(spec.category, sjs.CATEGORY_LABELS, f"{spec.id} 的分组未登记")

    def test_daily_schedule_text(self):
        self.assertEqual(_spec("check_vip_expiry").schedule_text, "每日 02:00")
        self.assertEqual(_spec("flush_view_counts").schedule_text, "每 1 分钟")

    def test_retention_notes_match_source_constants(self):
        """页面写的保留期必须与清理逻辑的常量一致（改常量忘改文案 → 运维按错的天数预估磁盘）"""
        from app.services.admin_audit_service import AUDIT_LOG_RETENTION_DAYS
        from app.services.content_view_service import CONTENT_VIEW_RETENTION_DAYS
        from app.services.search_log_service import SEARCH_LOG_RETENTION_DAYS

        cases = {
            "cleanup_audit_logs": AUDIT_LOG_RETENTION_DAYS,
            "cleanup_search_logs": SEARCH_LOG_RETENTION_DAYS,
            "cleanup_content_views": CONTENT_VIEW_RETENTION_DAYS,
        }
        for job_id, days in cases.items():
            with self.subTest(job=job_id):
                self.assertIn(f"保留 {days} 天", _spec(job_id).note)

    def test_legacy_exports_stay_in_sync(self):
        """`JOBS` / `STALE_SECONDS` 是给旧调用点的兼容导出，不能与目录脱钩"""
        self.assertEqual(sss.JOBS, tuple((spec.id, spec.label) for spec in sjs.JOB_SPECS))
        self.assertEqual(
            sss._JOB_STALE_SECONDS, {spec.id: spec.stale_seconds for spec in sjs.JOB_SPECS}
        )


def _registered_job_ids() -> list[str]:
    """调度器注册表里的任务 id（原先是正则扫 `add_job(id="...")` 字面量，
    改成配置驱动后没有字面量了，改成比对 `JOB_FUNCS` 对象）"""
    return list(scheduler_tasks.JOB_FUNCS)


class NextRunTests(unittest.TestCase):
    def test_daily_job_later_today(self):
        """凌晨 01:00 看 02:00 的任务 = 今天 02:00（不是明天）"""
        expected = datetime(2026, 10, 27, 2, 0, tzinfo=sjs.CST).timestamp()
        self.assertEqual(sjs.next_run_at(_spec("check_vip_expiry"), NOW), int(expected))

    def test_daily_job_already_passed_rolls_to_tomorrow(self):
        """01:00 看 00:30 的任务只能落到明天 —— 最容易写错的一格"""
        spec = sjs.JobSpec(
            id="x", label="x", category=sjs.CATEGORY_STORAGE, desc="", kind="daily", hour=0, minute=30
        )
        expected = datetime(2026, 10, 28, 0, 30, tzinfo=sjs.CST).timestamp()
        self.assertEqual(sjs.next_run_at(spec, NOW), int(expected))

    def test_daily_job_uses_beijing_time_not_utc(self):
        """同一时刻按 UTC 算是 17:00，按北京算是次日 01:00 —— 换算错就会整体差 8 小时"""
        # 北京时间 2026-10-27 01:00 == UTC 2026-10-26 17:00
        self.assertEqual(datetime.fromtimestamp(NOW, sjs.CST).hour, 1)
        next_run = datetime.fromtimestamp(sjs.next_run_at(_spec("check_vip_expiry"), NOW), sjs.CST)
        self.assertEqual((next_run.month, next_run.day, next_run.hour), (10, 27, 2))

    def test_interval_job_uses_last_run(self):
        spec = _spec("flush_view_counts")
        last = int(NOW) - 20
        self.assertEqual(sjs.next_run_at(spec, NOW, last), last + 60)

    def test_interval_job_ignores_ancient_last_run(self):
        """停机三天后「下次执行」不能是两天前（那是过去时间点，页面会显示得很怪）"""
        spec = _spec("flush_view_counts")
        last = int(NOW) - 3 * 24 * 3600
        self.assertEqual(sjs.next_run_at(spec, NOW, last), int(NOW) + 60)

    def test_interval_job_without_history(self):
        spec = _spec("flush_view_counts")
        self.assertEqual(sjs.next_run_at(spec, NOW, None), int(NOW) + 60)


class JobStatusTests(unittest.TestCase):
    def _build(self, heartbeat, job_id="check_vip_expiry"):
        return sjs.build_job(_spec(job_id), heartbeat, NOW, {})

    def test_missing_heartbeat_is_never_not_failed(self):
        item = self._build(None)
        self.assertEqual(item["status"], "never")
        self.assertIsNone(item["ok"])
        self.assertIsNone(item["last_run_at"])
        self.assertIn("无执行记录", item["stale_reason"])

    def test_recent_success_is_ok(self):
        item = self._build({"at": int(NOW) - 3600, "ok": True, "detail": "降级 2 个用户"})
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["detail"], "降级 2 个用户")
        self.assertEqual(item["last_run_at"], int(NOW) - 3600)

    def test_explicit_failure(self):
        item = self._build({"at": int(NOW) - 60, "ok": False, "detail": "执行失败"})
        self.assertEqual(item["status"], "failed")

    def test_failed_but_ancient_is_failed_first(self):
        """失败优先级高于超期：先让人看见「它失败了」，而不是「它三天没跑」"""
        item = self._build({"at": int(NOW) - 5 * 24 * 3600, "ok": False})
        self.assertEqual(item["status"], "failed")

    def test_stale_when_too_old(self):
        item = self._build({"at": int(NOW) - 30 * 3600, "ok": True})
        self.assertEqual(item["status"], "stale")
        self.assertIn("小时未执行", item["stale_reason"])

    def test_minutely_job_goes_stale_fast(self):
        """每分钟任务 30 分钟没跑就该报 —— 与日报任务的 26 小时阈值不能混用"""
        item = self._build({"at": int(NOW) - 45 * 60, "ok": True}, job_id="flush_view_counts")
        self.assertEqual(item["status"], "stale")

    def test_malformed_heartbeat_degrades_to_never(self):
        """心跳内容不是合法结构（手工塞的脏数据）不能把页面搞崩"""
        item = self._build({"at": "not-a-number", "ok": True})
        self.assertEqual(item["status"], "never")

    def test_requires_reports_configured_state(self):
        item = sjs.build_job(_spec("push_new_urls_to_baidu"), None, NOW, {"BAIDU_PUSH_TOKEN": False})
        self.assertEqual(item["requires"], [{"key": "BAIDU_PUSH_TOKEN", "configured": False}])

    def test_job_without_requires_has_empty_list(self):
        self.assertEqual(self._build(None)["requires"], [])


class OverviewTests(unittest.TestCase):
    def test_summary_counts_every_status(self):
        heartbeats = {
            "check_vip_expiry": {"at": int(NOW) - 60, "ok": True},
            "push_new_urls_to_baidu": {"at": int(NOW) - 60, "ok": False},
            "cleanup_audit_logs": {"at": int(NOW) - 40 * 3600, "ok": True},
        }
        overview = sjs.build_overview(heartbeats, NOW, {})
        summary = overview["summary"]
        self.assertEqual(summary["total"], len(sjs.JOB_SPECS))
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["stale"], 1)
        self.assertEqual(summary["never"], summary["total"] - 3)
        self.assertEqual(summary["attention"], summary["total"] - 1)

    def test_all_silent_is_reported_once(self):
        """全员无心跳 = 调度器大概率没起，这是一条结论而不是 8 条告警"""
        overview = sjs.build_overview({}, NOW, {})
        self.assertTrue(overview["all_silent"])
        self.assertEqual(overview["summary"]["never"], overview["summary"]["total"])

    def test_all_silent_false_when_any_heartbeat_exists(self):
        overview = sjs.build_overview({"check_vip_expiry": {"at": int(NOW), "ok": True}}, NOW, {})
        self.assertFalse(overview["all_silent"])

    def test_groups_cover_every_job_exactly_once(self):
        overview = sjs.build_overview({}, NOW, {})
        ids = [job["id"] for group in overview["groups"] for job in group["jobs"]]
        self.assertEqual(sorted(ids), sorted(spec.id for spec in sjs.JOB_SPECS))

    def test_unhealthy_jobs_come_first_in_group(self):
        """组内排序：失败 → 超期 → 从未执行 → 正常（页面默认顺序就要把问题顶上来）"""
        heartbeats = {
            "cleanup_audit_logs": {"at": int(NOW) - 60, "ok": True},
            "cleanup_search_logs": {"at": int(NOW) - 60, "ok": False},
            "cleanup_content_views": {"at": int(NOW) - 40 * 3600, "ok": True},
            # flush_view_counts 无心跳 → never
        }
        overview = sjs.build_overview(heartbeats, NOW, {})
        storage = next(group for group in overview["groups"] if group["key"] == sjs.CATEGORY_STORAGE)
        self.assertEqual(
            [job["status"] for job in storage["jobs"]],
            ["failed", "stale", "never", "never", "ok"],
        )

    def test_payload_survives_response_model(self):
        """接口用 ApiResponse[StatsResult] 做校验，字段不在模型里会被静默丢掉"""
        from app.schemas.admin import StatsResult

        overview = sjs.build_overview({}, NOW, {})
        dumped = StatsResult.model_validate(overview).model_dump()
        for key in ("summary", "groups", "all_silent", "timezone"):
            self.assertIn(key, dumped, f"响应模型丢字段: {key}")


class EndpointTests(unittest.TestCase):
    """接口契约：路由已注册 + 读写分权限

    需要数据库的页面级断言（载荷形状、调度器未运行时的降级）在
    `tests/test_scheduled_job_admin.py`，那里有最小会话替身。
    """

    def test_read_route_is_registered(self):
        from app.main import app

        self.assertIn("/api/v1/admin/scheduled-jobs", app.openapi()["paths"])
        self.assertIn("/api/v1/admin/scheduled-jobs/{job_id}", app.openapi()["paths"])
        self.assertIn("/api/v1/admin/scheduled-jobs/{job_id}/run", app.openapi()["paths"])

    def test_read_route_reuses_dashboard_permission(self):
        """只读运维接口复用 dashboard:view，不新建权限码（新码要配套补种迁移）"""
        from app.core.rbac import match_route_permission

        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/scheduled-jobs"), ["dashboard:view"]
        )

    def test_write_routes_require_manage_permission(self):
        """写操作单开 scheduler:manage —— 只读运维角色不该能停掉全站维护任务"""
        from app.core.rbac import match_route_permission

        self.assertEqual(
            match_route_permission("PUT", "/api/v1/admin/scheduled-jobs/flush_view_counts"),
            ["scheduler:manage"],
        )
        self.assertEqual(
            match_route_permission("POST", "/api/v1/admin/scheduled-jobs/flush_view_counts/run"),
            ["scheduler:manage"],
        )

    def test_manage_permission_is_in_catalog(self):
        """新权限码必须在目录里 + 有补种迁移（否则已有部署升级即 403）"""
        from app.services.rbac_catalog import ALL_PERMISSION_CODES

        self.assertIn("scheduler:manage", ALL_PERMISSION_CODES)
        migrations = (REPO_ROOT / "backend" / "alembic" / "versions").glob("*seed_scheduler_permission*.py")
        self.assertTrue(list(migrations), "缺少权限补种迁移，已有部署升级后管理员会 403")


if __name__ == "__main__":
    unittest.main()
