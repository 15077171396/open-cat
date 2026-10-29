"""运维状态聚合（后台「系统状态」卡）的契约测试。

这里钉的不是「函数能跑通」，而是五条**破了不会报错、只会让运维失去判断力**的契约：

1. **心跳绝不能反噬任务**：Redis 挂了，任务照常跑完（观测失败不等于任务失败）。
2. **单项失败不拖垮整页**：探针抛异常时接口仍返回结构化的「未知 + 告警」，
   而不是 500 —— 运维页打不开的时候，恰恰是最需要它的时候。
3. **不谎报**：备份目录不可见（本地开发/未挂载）只标「不可见」，**不产生告警**；
   库版本读不到时 `in_sync` 为 None 而不是 False。假告警比没有告警更糟。
4. **任务清单与调度器注册表一致**：`JOB_SPECS` 里写了但 `JOB_FUNCS` 没注册（或反之），
   后台会永远显示「无执行记录」，看起来像任务没跑，实际是名字写错了。
5. **注册进调度器的必须是心跳包装后的函数**：漏包一层，该任务在后台永远是「无记录」。
6. **已停用 ≠ 未按时执行**：人主动关掉的任务不该天天挂一条告警（见 `_collect_warnings`）。
"""

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.config import settings
from app.services import scheduled_job_service as sjs
from app.services import system_status_service as sss
from scheduler import tasks as scheduler_tasks
from tests.conftest import FakeRedis


class _BrokenRedis:
    """所有命令都失败的 Redis 替身（模拟 Redis 不可用）"""

    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            raise ConnectionError("redis down")

        return _fail


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_job_run_swallows_redis_failure(self):
        """Redis 不可用时写心跳不得抛异常（否则会把任务本身拖挂）"""
        with mock.patch.object(sss, "redis_client", _BrokenRedis()):
            await sss.record_job_run("check_vip_expiry", True, "ok")  # 不抛即通过

    async def test_record_data_quality_swallows_redis_failure(self):
        with mock.patch.object(sss, "redis_client", _BrokenRedis()):
            await sss.record_data_quality({"issue_total": 0})

    async def test_heartbeat_roundtrip(self):
        fake = FakeRedis()
        with mock.patch.object(sss, "redis_client", fake):
            await sss.record_job_run("check_vip_expiry", True, "降级 3 个用户")
            jobs = await sss._jobs_status()
        item = next(j for j in jobs if j["id"] == "check_vip_expiry")
        self.assertTrue(item["ok"])
        self.assertEqual(item["detail"], "降级 3 个用户")
        self.assertFalse(item["stale"])

    async def test_missing_heartbeat_is_stale(self):
        """从未跑过的任务必须显式标 stale（后台要能看出「它压根没跑过」）"""
        with mock.patch.object(sss, "redis_client", FakeRedis()):
            jobs = await sss._jobs_status()
        self.assertTrue(all(j["stale"] for j in jobs))
        self.assertTrue(all(j["stale_reason"] == "无执行记录" for j in jobs))

    async def test_old_heartbeat_is_stale(self):
        """超过阈值的心跳判 stale：日报任务 26h、每分钟任务 30min"""
        fake = FakeRedis()
        old = int(time.time()) - 3 * 24 * 3600
        await fake.set(f"{sss._JOB_KEY_PREFIX}flush_view_counts", f'{{"at": {old}, "ok": true}}')
        with mock.patch.object(sss, "redis_client", fake):
            jobs = await sss._jobs_status()
        item = next(j for j in jobs if j["id"] == "flush_view_counts")
        self.assertTrue(item["stale"])


class WarningTests(unittest.TestCase):
    def _status(self, **checks) -> dict:
        base = {"checks": {"mysql": {"ok": True}, "redis": {"ok": True}, "schema": {"in_sync": True},
                           "backup": {"visible": True, "stale": False}, "alerts": {"sentry": True, "watchdog_webhook": True},
                           "data_quality": {}}, "jobs": []}
        base["checks"].update(checks)
        return base

    def test_clean_status_has_no_warning(self):
        self.assertEqual(sss._collect_warnings(self._status()), [])

    def test_alert_channels_unset_are_reported(self):
        """三个空值是最容易忘的配置，必须出现在告警列表里"""
        warnings = sss._collect_warnings(
            self._status(alerts={"sentry": False, "watchdog_webhook": False})
        )
        texts = " ".join(w["text"] for w in warnings)
        self.assertIn("SENTRY_DSN", texts)
        self.assertIn("WATCHDOG_WEBHOOK", texts)

    def test_schema_mismatch_is_error(self):
        warnings = sss._collect_warnings(
            self._status(schema={"in_sync": False, "db_revision": "a", "code_head": "b"})
        )
        self.assertEqual(warnings[0]["level"], "error")
        self.assertIn("数据库版本与代码不一致", warnings[0]["text"])

    def test_schema_unknown_is_not_reported(self):
        """读不到版本 = 未知，不能当成不一致去报警（否则每次探针降级都刷一条假告警）"""
        warnings = sss._collect_warnings(
            self._status(schema={"in_sync": None, "db_revision": None, "code_head": None})
        )
        self.assertEqual(warnings, [])

    def test_backup_invisible_is_silent(self):
        """目录不可见（本地开发 / 容器未挂载）不告警 —— 假告警会消磨信任"""
        warnings = sss._collect_warnings(
            self._status(backup={"visible": False, "stale": None, "age_hours": None})
        )
        self.assertEqual(warnings, [])

    def test_backup_empty_dir_is_warned(self):
        warnings = sss._collect_warnings(
            self._status(backup={"visible": True, "stale": True, "age_hours": None})
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("最近备份为空", warnings[0]["text"])

    def test_job_failure_and_stale_are_reported(self):
        warnings = sss._collect_warnings(
            self._status()
            | {"jobs": [
                {"id": "a", "label": "任务A", "stale": True, "stale_reason": "已 3 小时未执行", "ok": None},
                {"id": "b", "label": "任务B", "stale": False, "ok": False},
            ]}
        )
        texts = " ".join(w["text"] for w in warnings)
        self.assertIn("任务A", texts)
        self.assertIn("任务B", texts)

    def test_data_quality_issue_is_reported(self):
        warnings = sss._collect_warnings(self._status(data_quality={"issue_total": 7}))
        self.assertIn("7 项异常", warnings[0]["text"])


class FileCheckTests(unittest.TestCase):
    def test_backup_dir_missing_is_invisible_not_stale(self):
        with mock.patch.object(settings, "BACKUP_DIR", str(Path(tempfile.gettempdir()) / "no-such-backup-dir")):
            status = sss._backup_status()
        self.assertFalse(status["visible"])
        self.assertIsNone(status["stale"])

    def test_backup_age_and_stale_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "ai_cat_20260101.sql.gz"
            backup.write_bytes(b"x")
            old = time.time() - 3 * 24 * 3600
            os.utime(backup, (old, old))
            with mock.patch.object(settings, "BACKUP_DIR", tmp):
                status = sss._backup_status()
        self.assertTrue(status["visible"])
        self.assertEqual(status["latest"], backup.name)
        self.assertGreater(status["age_hours"], 48)
        self.assertTrue(status["stale"])

    def test_backup_dir_without_sql_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(settings, "BACKUP_DIR", tmp):
                status = sss._backup_status()
        self.assertTrue(status["visible"])
        self.assertIsNone(status["latest"])
        self.assertTrue(status["stale"])

    def test_log_dir_status_reports_recent_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app.log").write_text("x", encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOG_DIR": tmp}):
                status = sss._log_dir_status()
        self.assertTrue(status["visible"])
        self.assertTrue(status["writable"])
        self.assertIsNotNone(status["last_write_age_minutes"])
        self.assertLess(status["last_write_age_minutes"], 5)

    def test_log_dir_missing_is_visible_false(self):
        with mock.patch.dict(os.environ, {"LOG_DIR": str(Path(tempfile.gettempdir()) / "no-such-log-dir")}):
            status = sss._log_dir_status()
        self.assertFalse(status["visible"])


class AggregateTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_failure_degrades_instead_of_raising(self):
        """探针自己炸了也要给出可读结论（运维页不能在最需要时 500）"""
        with mock.patch.object(sss, "redis_client", FakeRedis()):
            with mock.patch.object(sss.health_service, "get_deep_health", side_effect=RuntimeError("boom")):
                status = await sss.get_system_status()
        self.assertIn("checks", status)
        self.assertIn("warnings", status)
        self.assertTrue(status["warnings"], "依赖未知时至少要有一条告警")

    async def test_result_is_cached(self):
        fake = FakeRedis()
        calls = {"n": 0}

        async def _deep():
            calls["n"] += 1
            return {"status": "ok", "checks": {"mysql": {"ok": True}, "redis": {"ok": True}}}, True

        with mock.patch.object(sss, "redis_client", fake):
            with mock.patch.object(sss.health_service, "get_deep_health", _deep):
                await sss.get_system_status()
                await sss.get_system_status()
        self.assertEqual(calls["n"], 1, "第二次应命中缓存，不应重复探测")

    async def test_cache_disabled_by_zero_ttl(self):
        fake = FakeRedis()
        calls = {"n": 0}

        async def _deep():
            calls["n"] += 1
            return {"status": "ok", "checks": {"mysql": {"ok": True}, "redis": {"ok": True}}}, True

        with mock.patch.object(sss, "redis_client", fake):
            with mock.patch.object(settings, "SYSTEM_STATUS_CACHE_SECONDS", 0):
                with mock.patch.object(sss.health_service, "get_deep_health", _deep):
                    await sss.get_system_status()
                    await sss.get_system_status()
        self.assertEqual(calls["n"], 2)


class RouteAndSchemaTests(unittest.IsolatedAsyncioTestCase):
    """接口契约：路由已注册 + 权限码复用 dashboard:view + 响应模型能收下这份 payload

    第三条最容易被忽略：`response_model=ApiResponse[StatsResult]` 会在返回前做校验，
    字段一旦不在模型声明里（且模型没开 extra=allow），FastAPI 会**静默丢字段**或直接 500。
    """

    def test_route_is_registered(self):
        from app.main import app

        self.assertIn("/api/v1/admin/system-status", app.openapi()["paths"])

    def test_route_reuses_dashboard_permission(self):
        from app.core.rbac import match_route_permission

        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/system-status"),
            ["dashboard:view"],
            "新增只读运维接口必须复用 dashboard:view，不要新建权限码",
        )

    async def test_payload_survives_response_model(self):
        from app.schemas.admin import StatsResult

        with mock.patch.object(sss, "redis_client", FakeRedis()):
            payload = await sss.get_system_status()
        dumped = StatsResult.model_validate(payload).model_dump()
        for key in ("checks", "jobs", "warnings", "env"):
            self.assertIn(key, dumped, f"响应模型丢字段: {key}")


class _FakeScheduler:
    """APScheduler 最小替身：只记 add/remove，够断言「谁被注册/移除了」"""

    def __init__(self):
        self.jobs: dict[str, object] = {}

    def add_job(self, func, trigger, id, **kwargs):  # noqa: A002 参数名与 APScheduler 一致
        self.jobs[id] = func

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


class SchedulerGuardTests(unittest.TestCase):
    """跨文件守卫：JOB_SPECS ↔ JOB_FUNCS ↔ 心跳包装，三者必须完全一致

    调度方式改成「库里的配置驱动」之后，静态 `add_job(id="...")` 的字面量不存在了，
    于是原来的正则比对失效（会静默变成空断言）。这里改为**对象级**比对：
    注册表键集合必须与目录 id 集合相等，且注册进 scheduler 的必须是心跳包装后的函数
    （原始 handler 的身份比对 —— 漏包一层心跳就红）。
    """

    def test_every_declared_job_has_handler(self):
        declared = {spec.id for spec in sjs.JOB_SPECS}
        missing = declared - set(scheduler_tasks.JOB_FUNCS)
        self.assertFalse(missing, f"目录里声明但 JOB_FUNCS 没注册（后台永远显示「从未执行」）: {sorted(missing)}")

    def test_every_handler_is_declared(self):
        """反向：注册了 handler 却没进目录 → 后台看不到、也没法配置周期"""
        declared = {spec.id for spec in sjs.JOB_SPECS}
        extra = set(scheduler_tasks.JOB_FUNCS) - declared
        self.assertFalse(extra, f"JOB_FUNCS 里有但目录未声明（后台不可见）: {sorted(extra)}")

    def test_handlers_are_callable(self):
        for job_id, handler in scheduler_tasks.JOB_FUNCS.items():
            with self.subTest(job=job_id):
                self.assertTrue(callable(handler), f"{job_id} 的 handler 不可调用")

    def test_scheduled_callable_is_the_heartbeat_wrapper(self):
        """真正注册进调度器的必须是包装后的函数（不是裸 handler）"""
        fake = FakeRedis()
        sched = _FakeScheduler()
        configs = {spec.id: sjs.default_config(spec) for spec in sjs.JOB_SPECS}

        async def _go():
            with mock.patch.object(scheduler_tasks, "redis_client", fake):
                await scheduler_tasks._apply_configs(sched, configs)

        asyncio.run(_go())
        self.assertEqual(set(sched.jobs), set(scheduler_tasks.JOB_FUNCS))
        for job_id, registered in sched.jobs.items():
            with self.subTest(job=job_id):
                self.assertIsNot(
                    registered,
                    scheduler_tasks.JOB_FUNCS[job_id],
                    f"{job_id} 直接注册了裸 handler，心跳不会被写（后台永远「无执行记录」）",
                )
                # functools.wraps 保留原函数身份，便于日志/排错时认出是哪一个任务
                self.assertIs(getattr(registered, "__wrapped__", None), scheduler_tasks.JOB_FUNCS[job_id])

    def test_disabled_job_is_not_scheduled(self):
        fake = FakeRedis()
        sched = _FakeScheduler()
        configs = {spec.id: sjs.default_config(spec) for spec in sjs.JOB_SPECS}
        configs["flush_view_counts"] = sjs.JobConfig(
            enabled=False, kind="interval", hour=0, minute=0, interval_minutes=1
        )

        async def _go():
            with mock.patch.object(scheduler_tasks, "redis_client", fake):
                await scheduler_tasks._apply_configs(sched, configs)

        asyncio.run(_go())
        self.assertNotIn("flush_view_counts", sched.jobs)
        self.assertEqual(len(sched.jobs), len(configs) - 1)

    def test_unchanged_config_does_not_reapply(self):
        """配置没变时不得重复 add_job —— replace_existing 会重排 next_run_time，
        每 5 秒重排一次等于让「每 1 分钟」的任务永远等不到下一次执行。"""
        fake = FakeRedis()
        sched = _FakeScheduler()
        adds = {"n": 0}
        original_add = sched.add_job

        def _counting_add(*args, **kwargs):
            adds["n"] += 1
            return original_add(*args, **kwargs)

        sched.add_job = _counting_add

        async def _go():
            with mock.patch.object(scheduler_tasks, "redis_client", fake):
                with mock.patch.object(
                    scheduler_tasks, "_load_configs", _fake_load_configs
                ):
                    scheduler_tasks._applied_hash = None
                    await scheduler_tasks._sync_once(sched)
                    first = adds["n"]
                    await scheduler_tasks._sync_once(sched)
                    return first, adds["n"]

        first, total = asyncio.run(_go())
        self.assertEqual(first, len(scheduler_tasks.JOB_FUNCS))
        self.assertEqual(total, first, "第二次同步不应重新注册任何任务")

    def test_heartbeat_treats_false_as_failure(self):
        """失败判定的契约：任务返回 False = 失败（不靠异常，因为任务刻意吞异常）"""

        async def _fail():
            return False

        async def _ok():
            return "完成"

        recorded = []

        async def _record(job_id, ok, detail=""):
            recorded.append((job_id, ok, detail))

        with mock.patch.object(scheduler_tasks, "record_job_run", _record):
            asyncio.run(scheduler_tasks._with_heartbeat("j1", _fail)())
            asyncio.run(scheduler_tasks._with_heartbeat("j2", _ok)())

        self.assertEqual(recorded[0][1], False)
        self.assertEqual(recorded[1][1], True)
        self.assertEqual(recorded[1][2], "完成")

    def test_heartbeat_records_failure_when_task_raises(self):
        """异常路径必须记成失败：任务里吞掉的异常靠返回值判定，漏网的异常靠这里兜底"""

        async def _boom():
            raise ValueError("x")

        recorded = []

        async def _record(job_id, ok, detail=""):
            recorded.append((job_id, ok, detail))

        with mock.patch.object(scheduler_tasks, "record_job_run", _record):
            with self.assertRaises(ValueError):
                asyncio.run(scheduler_tasks._with_heartbeat("j3", _boom)())
        self.assertEqual(recorded[0][1], False, "异常必须记为失败，否则后台会显示绿色")
        self.assertIn("ValueError", recorded[0][2])


async def _fake_load_configs():
    """测试用：不碰数据库的配置来源（全部用代码默认值）"""
    return {spec.id: sjs.default_config(spec) for spec in sjs.JOB_SPECS}


class ManualTriggerTests(unittest.TestCase):
    """「立即执行一次」的消费端：命令队列 → 执行 → 心跳带 [手动] 标记"""

    def test_command_runs_registered_job(self):
        fake = FakeRedis()
        recorded = []
        calls = []

        async def _handler():
            calls.append(1)
            return "手动跑完"

        async def _record(job_id, ok, detail=""):
            recorded.append((job_id, ok, detail))

        async def _go():
            await fake.lpush("ops:jobs:commands", '{"job_id": "check_vip_expiry", "by": "ops"}')
            with mock.patch.object(scheduler_tasks, "redis_client", fake):
                with mock.patch.dict(scheduler_tasks.JOB_FUNCS, {"check_vip_expiry": _handler}):
                    with mock.patch.object(scheduler_tasks, "record_job_run", _record):
                        raw = await fake.lpop("ops:jobs:commands")
                        await scheduler_tasks._handle_command(raw)

        asyncio.run(_go())
        self.assertEqual(calls, [1])
        self.assertEqual(recorded[0][0], "check_vip_expiry")
        self.assertTrue(recorded[0][1])
        self.assertIn("[手动]", recorded[0][2])

    def test_unknown_job_command_is_ignored(self):
        """队列里混进未知 id（旧命令/手改）只记日志，不能炸掉消费循环"""
        recorded = []

        async def _record(job_id, ok, detail=""):
            recorded.append(job_id)

        async def _go():
            with mock.patch.object(scheduler_tasks, "record_job_run", _record):
                await scheduler_tasks._handle_command('{"job_id": "no_such_job"}')

        asyncio.run(_go())
        self.assertEqual(recorded, [], "未知任务不应产生心跳")

    def test_malformed_command_is_ignored(self):
        async def _go():
            await scheduler_tasks._handle_command("not-json")

        asyncio.run(_go())  # 不抛即通过

    def test_same_job_does_not_run_twice_concurrently(self):
        """并发闩锁：同一任务正在跑时，第二条触发直接忽略（清理类任务重复跑会互相踩）"""
        started = []
        finished = []

        async def _slow():
            started.append(1)
            await asyncio.sleep(0.05)
            finished.append(1)
            return "ok"

        async def _record(job_id, ok, detail=""):
            pass

        async def _go():
            with mock.patch.object(scheduler_tasks, "record_job_run", _record):
                await asyncio.gather(
                    scheduler_tasks._run_guarded("j_concurrent", _slow),
                    scheduler_tasks._run_guarded("j_concurrent", _slow),
                )

        asyncio.run(_go())
        self.assertEqual(len(started), 1, "同一任务不应并发执行两次")


if __name__ == "__main__":
    unittest.main()
