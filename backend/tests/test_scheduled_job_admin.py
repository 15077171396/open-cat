"""定时任务「可管理」面的契约测试（启停 / 改周期 / 立即执行一次）。

这里钉的不是「函数能跑通」，而是五条**破了不会报错、只会让人误以为已经生效**的契约：

1. **保存 ≠ 生效**：配置写进库后 `apply.pending` 必须为 True，直到调度器对齐；
   否则用户改完周期看不到变化，会以为没保存成功而反复点。
2. **调度器没在跑时必须报错**：把命令塞进无人消费的队列，等于骗管理员「已经跑过了」。
3. **已停用 ≠ 未按时执行**：人主动关掉的任务不该天天挂告警（否则真故障被淹没）。
4. **每次写操作都留审计**：启停能静默改变全站维护行为（VIP 不降级、浏览量停更）。
5. **配置没变化就不写库、不写审计**：前端重复保存不该制造噪音。

会话用最小替身（`_FakeSession`）：只实现本模块用到的 execute/get/add/commit，
不引入内存 SQL 仿真 —— 那层面向资金链路，用在这里只会带来假绿。
"""

import json
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from sqlalchemy.exc import OperationalError

from app.core.exceptions import AppException, NotFoundError
from app.services import scheduled_job_admin_service as job_admin
from app.services import scheduled_job_service as sjs
from app.services import system_status_service as sss
from tests.conftest import FakeRedis

def _now() -> float:
    """取当前时间（不要在模块导入时冻结：整套测试跑过 60 秒后会误判调度器死亡）"""
    return time.time()


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """只实现 `list_jobs` / `update_job` / `trigger_run` 触达的接口"""

    def __init__(self, rows=None, *, fail_select: bool = False):
        self.rows = list(rows or [])
        self.fail_select = fail_select
        self.added: list = []
        self.commits = 0

    async def execute(self, _stmt):
        if self.fail_select:
            raise OperationalError("SELECT", {}, Exception("Table 'scheduled_job_configs' doesn't exist"))
        return _Result(self.rows)

    async def get(self, _model, key):
        return next((row for row in self.rows if row.job_id == key), None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _row(job_id: str, **overrides):
    """一行配置（字段与 ORM 对齐，用 SimpleNamespace 是因为只需要属性访问）"""
    base = {
        "job_id": job_id,
        "enabled": True,
        "schedule_kind": "daily",
        "hour": 2,
        "minute": 0,
        "interval_minutes": None,
        "updated_by": None,
        "updated_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _admin():
    return SimpleNamespace(id=1, account="ops_admin")


def _applied(hash_: str, at: float | None = None):
    return json.dumps({"hash": hash_, "at": int(time.time() if at is None else at), "jobs": {}})


def _default_hash() -> str:
    return sjs.config_fingerprint(dict(sjs.DEFAULT_CONFIGS))


class _RedisDown:
    """所有命令都失败的 Redis 替身（刻意**不继承** FakeRedis：继承会让 lpush 等
    真实方法命中类属性，`__getattr__` 根本不会被调用，测出来的「失败」是假象）"""

    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            raise ConnectionError("redis down")

        return _fail


# ---------------------------------------------------------------------------
# 页面载荷
# ---------------------------------------------------------------------------


class ListJobsTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_rows_render_every_job(self):
        fake = FakeRedis()
        with mock.patch.object(sss, "redis_client", fake):
            data = await job_admin.list_jobs(_FakeSession())
        self.assertEqual(data["summary"]["total"], len(sjs.JOB_SPECS))
        self.assertTrue(data["configs_available"])
        ids = [job["id"] for group in data["groups"] for job in group["jobs"]]
        self.assertEqual(sorted(ids), sorted(spec.id for spec in sjs.JOB_SPECS))

    async def test_no_applied_marker_means_pending_and_dead(self):
        """调度器没起过：配置一定「未生效」，且 must 报 alive=False（页面据此禁用执行按钮）"""
        fake = FakeRedis()
        with mock.patch.object(sss, "redis_client", fake):
            data = await job_admin.list_jobs(_FakeSession())
        self.assertTrue(data["apply"]["pending"])
        self.assertFalse(data["apply"]["alive"])

    async def test_matching_hash_means_applied(self):
        fake = FakeRedis()
        await fake.set(sjs.APPLIED_KEY, _applied(_default_hash(), _now()))
        with mock.patch.object(sss, "redis_client", fake):
            data = await job_admin.list_jobs(_FakeSession())
        self.assertFalse(data["apply"]["pending"])
        self.assertTrue(data["apply"]["alive"])

    async def test_changed_config_is_pending_until_scheduler_catches_up(self):
        """库里的配置与调度器记的指纹不一致 → 必须显示「待生效」"""
        fake = FakeRedis()
        await fake.set(sjs.APPLIED_KEY, _applied("deadbeef0000", _now()))
        rows = [_row("check_vip_expiry", hour=3)]
        with mock.patch.object(sss, "redis_client", fake):
            data = await job_admin.list_jobs(_FakeSession(rows))
        self.assertTrue(data["apply"]["pending"])
        self.assertTrue(data["apply"]["alive"])

    async def test_stale_marker_means_dead_scheduler(self):
        """标记存在但已过期（>60 秒没刷新）→ 调度器挂了，同样不能执行"""
        fake = FakeRedis()
        await fake.set(sjs.APPLIED_KEY, _applied(_default_hash(), _now() - 600))
        with mock.patch.object(sss, "redis_client", fake):
            data = await job_admin.list_jobs(_FakeSession())
        self.assertFalse(data["apply"]["alive"])

    async def test_missing_table_degrades_instead_of_raising(self):
        """迁移没执行时页面照常打开，但明确标「配置表不可用」"""
        fake = FakeRedis()
        with mock.patch.object(sss, "redis_client", fake):
            data = await job_admin.list_jobs(_FakeSession(fail_select=True))
        self.assertFalse(data["configs_available"])
        self.assertEqual(data["summary"]["total"], len(sjs.JOB_SPECS))

    async def test_customized_flag_marks_changed_schedule(self):
        fake = FakeRedis()
        rows = [_row("check_vip_expiry", hour=6, minute=30)]
        with mock.patch.object(sss, "redis_client", fake):
            data = await job_admin.list_jobs(_FakeSession(rows))
        job = next(
            job for group in data["groups"] for job in group["jobs"] if job["id"] == "check_vip_expiry"
        )
        self.assertTrue(job["customized"])
        self.assertEqual(job["schedule"], "每日 06:30")
        self.assertEqual(job["default_schedule"], "每日 02:00")

    async def test_disabled_job_is_not_counted_as_attention(self):
        """停用不算「需要处理」——它是人的决定，不是故障"""
        fake = FakeRedis()
        rows = [_row("check_vip_expiry", enabled=False)]
        with mock.patch.object(sss, "redis_client", fake):
            data = await job_admin.list_jobs(_FakeSession(rows))
        summary = data["summary"]
        self.assertEqual(summary["disabled"], 1)
        self.assertEqual(summary["attention"], summary["total"] - summary["disabled"])
        job = next(
            job for group in data["groups"] for job in group["jobs"] if job["id"] == "check_vip_expiry"
        )
        self.assertEqual(job["status"], "disabled")
        self.assertIsNone(job["next_run_at"], "停用的任务不该给出「下次执行」时间")


# ---------------------------------------------------------------------------
# 改配置
# ---------------------------------------------------------------------------


class UpdateJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_disable_writes_row_and_audit(self):
        db = _FakeSession([_row("check_vip_expiry")])
        result = await job_admin.update_job(db, _admin(), "check_vip_expiry", enabled=False)
        self.assertTrue(result["changed"])
        self.assertFalse(db.rows[0].enabled)
        self.assertEqual(db.commits, 1)
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].action, "scheduled_job_update")
        self.assertEqual(db.added[0].target_type, "scheduled_job")
        self.assertIn("停用", db.added[0].summary)

    async def test_switch_to_interval_clears_daily_fields(self):
        """切到「每 N 分钟」后必须把 hour/minute 清空 —— 残留值会让库里的数据和生效周期对不上"""
        db = _FakeSession([_row("check_vip_expiry")])
        await job_admin.update_job(
            db, _admin(), "check_vip_expiry", schedule_kind="interval", interval_minutes=30
        )
        row = db.rows[0]
        self.assertEqual(row.schedule_kind, "interval")
        self.assertEqual(row.interval_minutes, 30)
        self.assertIsNone(row.hour)
        self.assertIsNone(row.minute)

    async def test_update_existing_interval_job_to_daily(self):
        db = _FakeSession([_row("flush_view_counts", schedule_kind="interval", interval_minutes=1, hour=None)])
        await job_admin.update_job(db, _admin(), "flush_view_counts", schedule_kind="daily", hour=6, minute=5)
        row = db.rows[0]
        self.assertEqual((row.schedule_kind, row.hour, row.minute), ("daily", 6, 5))
        self.assertIsNone(row.interval_minutes)

    async def test_creates_row_when_missing(self):
        """库里没有这行（新加任务但迁移没跟着补）时保存要能落库，而不是静默失败"""
        db = _FakeSession([])
        await job_admin.update_job(db, _admin(), "check_vip_expiry", enabled=False)
        self.assertEqual(len(db.rows), 0, "替身不会自动收进 rows（add 由断言覆盖）")
        created = [obj for obj in db.added if getattr(obj, "job_id", None) == "check_vip_expiry"]
        self.assertTrue(created, "缺失的行应通过 db.add 新建")

    async def test_no_change_writes_nothing(self):
        """重复保存同一份配置：不写库、不写审计（避免噪音审计把真操作埋掉）"""
        db = _FakeSession([_row("check_vip_expiry", hour=2, minute=0)])
        result = await job_admin.update_job(
            db, _admin(), "check_vip_expiry", schedule_kind="daily", hour=2, minute=0, enabled=True
        )
        self.assertFalse(result["changed"])
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.added, [])

    async def test_rejects_invalid_hour(self):
        db = _FakeSession([_row("check_vip_expiry")])
        with self.assertRaises(AppException) as ctx:
            await job_admin.update_job(db, _admin(), "check_vip_expiry", hour=24)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.commits, 0)

    async def test_rejects_invalid_interval(self):
        db = _FakeSession([_row("flush_view_counts", schedule_kind="interval", interval_minutes=1)])
        for bad in (0, 10081):
            with self.subTest(interval=bad):
                with self.assertRaises(AppException) as ctx:
                    await job_admin.update_job(
                        db, _admin(), "flush_view_counts", schedule_kind="interval", interval_minutes=bad
                    )
                self.assertEqual(ctx.exception.code, 400)

    async def test_rejects_unknown_job(self):
        with self.assertRaises(NotFoundError):
            await job_admin.update_job(_FakeSession([]), _admin(), "no_such_job", enabled=False)

    async def test_audit_keeps_old_and_new_values(self):
        """审计要能回答「原来是什么」—— 否则事后不知道是从 02:00 改成了 06:00"""
        db = _FakeSession([_row("check_vip_expiry", hour=2, minute=0)])
        await job_admin.update_job(db, _admin(), "check_vip_expiry", hour=6, minute=30)
        detail = db.added[0].detail
        self.assertEqual(detail["from"]["hour"], 2)
        self.assertEqual(detail["to"]["hour"], 6)
        self.assertEqual(detail["to"]["minute"], 30)


# ---------------------------------------------------------------------------
# 立即执行
# ---------------------------------------------------------------------------


class TriggerRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_when_scheduler_is_dead(self):
        """调度器没在跑：必须 503，不能假装入队成功"""
        fake = FakeRedis()  # 没有 applied 标记
        with mock.patch.object(sss, "redis_client", fake), mock.patch.object(job_admin, "redis_client", fake):
            with self.assertRaises(AppException) as ctx:
                await job_admin.trigger_run(_FakeSession([]), _admin(), "check_vip_expiry")
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(await fake.llen(sjs.COMMAND_QUEUE_KEY), 0, "拒绝时不得留下队列命令")

    async def test_enqueues_command_and_audits(self):
        fake = FakeRedis()
        await fake.set(sjs.APPLIED_KEY, _applied(_default_hash(), _now()))
        db = _FakeSession([])
        with mock.patch.object(sss, "redis_client", fake), mock.patch.object(job_admin, "redis_client", fake):
            result = await job_admin.trigger_run(db, _admin(), "check_vip_expiry")
        self.assertTrue(result["queued"])
        self.assertEqual(await fake.llen(sjs.COMMAND_QUEUE_KEY), 1)
        payload = json.loads((await fake.lpop(sjs.COMMAND_QUEUE_KEY)))
        self.assertEqual(payload["job_id"], "check_vip_expiry")
        self.assertEqual(payload["by"], "ops_admin")
        self.assertEqual(db.added[0].action, "scheduled_job_run")

    async def test_cooldown_blocks_double_click(self):
        fake = FakeRedis()
        await fake.set(sjs.APPLIED_KEY, _applied(_default_hash(), _now()))
        with mock.patch.object(sss, "redis_client", fake), mock.patch.object(job_admin, "redis_client", fake):
            await job_admin.trigger_run(_FakeSession([]), _admin(), "check_vip_expiry")
            with self.assertRaises(AppException) as ctx:
                await job_admin.trigger_run(_FakeSession([]), _admin(), "check_vip_expiry")
        self.assertEqual(ctx.exception.code, 429)
        self.assertEqual(await fake.llen(sjs.COMMAND_QUEUE_KEY), 1, "冷却拦截不应再入队")

    async def test_redis_failure_on_enqueue_is_reported(self):
        """队列写不进去时必须报错 —— 静默失败等于骗人说「已下发」"""
        fake = _RedisDown()
        with mock.patch.object(sss, "read_json", _alive_reader), mock.patch.object(job_admin, "redis_client", fake):
            with self.assertRaises(AppException) as ctx:
                await job_admin.trigger_run(_FakeSession([]), _admin(), "check_vip_expiry")
        self.assertEqual(ctx.exception.code, 503)

    async def test_rejects_unknown_job(self):
        fake = FakeRedis()
        await fake.set(sjs.APPLIED_KEY, _applied(_default_hash(), _now()))
        with mock.patch.object(sss, "redis_client", fake), mock.patch.object(job_admin, "redis_client", fake):
            with self.assertRaises(NotFoundError):
                await job_admin.trigger_run(_FakeSession([]), _admin(), "no_such_job")


async def _alive_reader(_key):
    """让存活判断通过（避免为了测入队失败而真的搭一套 Redis）"""
    return {"hash": "x", "at": int(time.time()), "jobs": {}}


class AlarmSuppressionTests(unittest.TestCase):
    """已停用的任务不能再刷「未按时执行」"""

    def _status(self, **job_overrides):
        job = {
            "id": "check_vip_expiry",
            "label": "VIP 过期降级",
            "enabled": True,
            "stale": True,
            "stale_reason": "已 30 小时未执行",
            "ok": None,
        }
        job.update(job_overrides)
        return {
            "checks": {
                "mysql": {"ok": True},
                "redis": {"ok": True},
                "schema": {"in_sync": True},
                "backup": {"visible": True, "stale": False},
                "alerts": {"sentry": True, "watchdog_webhook": True},
                "data_quality": {},
            },
            "jobs": [job],
        }

    def test_disabled_job_produces_no_warning(self):
        self.assertEqual(sss._collect_warnings(self._status(enabled=False)), [])

    def test_enabled_stale_job_still_warns(self):
        warnings = sss._collect_warnings(self._status())
        self.assertEqual(len(warnings), 1)
        self.assertIn("VIP 过期降级", warnings[0]["text"])


if __name__ == "__main__":
    unittest.main()
