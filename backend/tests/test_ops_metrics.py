"""核心指标快照契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **单项采集失败降级为 0，不拖垮整帧快照**（degrade_guard 收口）——
   某张表暂时不可读时快照宁可缺一项，也不能整帧缺失让趋势断线。
2. **写入快照与清理旧行不 commit**：事务由调度器任务体持有，失败整体回滚，
   不会出现「写了新帧没删旧行」的半截状态。
3. **trend 的指标键白名单校验**：未知键直接 400（下发给前端的选项与
   METRIC_LABELS 同源，前端不会出现第三种键）。
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from app.core.exceptions import AppException
from app.models.user import User
from app.services import ops_metric_service as oms
from app.models.ops_metric_snapshot import OpsMetricSnapshot


def _run(coro):
    return asyncio.run(coro)


def _result(value=None, rowcount=0, rows=None):
    return SimpleNamespace(
        scalar=lambda: value,
        rowcount=rowcount,
        all=lambda: rows or [],
        scalars=lambda: SimpleNamespace(all=lambda: rows or []),
    )


def _fake_db(results=None):
    """按调用顺序返回预设结果的假数据库会话；记录 execute 参数与 add 的对象"""
    calls: list = []
    added: list = []
    queue = list(results or [])

    async def _call(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else kwargs.get("params"))
        if queue:
            return queue.pop(0)
        return _result()

    return SimpleNamespace(execute=_call, add=added.append, delete=lambda *a: None), calls, added


def _snapshot_row(taken_at: datetime, metrics: dict):
    return SimpleNamespace(taken_at=taken_at, id=1, metrics=metrics)


class CollectTests(unittest.TestCase):
    def test_collect_metrics_returns_all_keys(self):
        # collect_metrics 顺序固定：users/tutorials/comments/feedbacks/moderation/login_failures
        db, _, _ = _fake_db(
            [
                _result(value=10),  # users_total
                _result(value=3),  # tutorials_total
                _result(value=7),  # comments_total
                _result(value=1),  # feedbacks_pending
                _result(value=2),  # moderation_pending
                _result(value=4),  # login_failures_1h
            ]
        )
        metrics = _run(oms.collect_metrics(db))
        self.assertEqual(
            metrics,
            {
                "users_total": 10,
                "tutorials_total": 3,
                "comments_total": 7,
                "feedbacks_pending": 1,
                "moderation_pending": 2,
                "login_failures_1h": 4,
            },
        )

    def test_single_metric_failure_degrades_to_zero(self):
        db, _, _ = _fake_db(
            [
                _result(value=10),
                RuntimeError("table missing"),  # 第 2 项失败 → 0
                _result(value=7),
                _result(value=1),
                _result(value=2),
                _result(value=4),
            ]
        )
        metrics = _run(oms.collect_metrics(db))
        self.assertEqual(metrics["tutorials_total"], 0)
        self.assertEqual(metrics["comments_total"], 7)

    def test_none_scalar_counts_as_zero(self):
        db, _, _ = _fake_db([_result(value=None)] * 6)
        metrics = _run(oms.collect_metrics(db))
        for value in metrics.values():
            self.assertEqual(value, 0)


class SnapshotTests(unittest.TestCase):
    def test_take_snapshot_adds_row_and_cleans_expired_without_commit(self):
        db, calls, added = _fake_db([_result(value=5)] * 6 + [_result(rowcount=0)])
        metrics = _run(oms.take_snapshot(db))
        self.assertEqual(len(added), 1)
        self.assertIsInstance(added[0], OpsMetricSnapshot)
        self.assertEqual(added[0].metrics, metrics)
        self.assertEqual(metrics["users_total"], 5)
        # 清理旧行的参数是 180 天前的 cutoff
        self.assertIn("cutoff", calls[-1])
        # 不 commit：事务由调度器任务体持有
        self.assertFalse(hasattr(db, "committed") and db.committed)

    def test_trend_returns_points_in_order(self):
        db, _, _ = _fake_db(
            [
                _result(
                    rows=[
                        _snapshot_row(datetime(2026, 11, 18, 1, 0), {"users_total": 1}),
                        _snapshot_row(datetime(2026, 11, 18, 2, 0), {"users_total": 2, "tutorials_total": 9}),
                        _snapshot_row(datetime(2026, 11, 18, 3, 0), {"tutorials_total": 5}),  # 缺 users_total → 跳过
                    ]
                )
            ]
        )
        data = _run(oms.trend(db, "users_total", hours=24))
        self.assertTrue(data["available"])
        self.assertEqual(data["label"], "注册用户总量")
        self.assertEqual([p["count"] for p in data["points"]], [1, 2])

    def test_trend_rejects_unknown_metric(self):
        db, _, _ = _fake_db([])
        with self.assertRaises(AppException):
            _run(oms.trend(db, "hacker_metric"))

    def test_trend_degrades_when_table_missing(self):
        db, _, _ = _fake_db([])
        db.execute = mock.AsyncMock(side_effect=RuntimeError("table missing"))
        data = _run(oms.trend(db, "users_total"))
        self.assertFalse(data["available"])
        self.assertEqual(data["points"], [])


class SummaryTests(unittest.TestCase):
    def test_summary_compares_two_frames(self):
        db, _, _ = _fake_db(
            [
                _result(
                    rows=[
                        _snapshot_row(datetime(2026, 11, 19, 2, 0), {"users_total": 105, "login_failures_1h": 3}),
                        _snapshot_row(datetime(2026, 11, 19, 1, 0), {"users_total": 100, "login_failures_1h": 7}),
                    ]
                )
            ]
        )
        data = _run(oms.latest_summary(db))
        self.assertTrue(data["available"])
        self.assertEqual(data["frame_count"], 2)
        by_key = {item["key"]: item for item in data["items"]}
        self.assertEqual(by_key["users_total"]["delta"], 5)
        self.assertEqual(by_key["login_failures_1h"]["delta"], -4)
        # 语义方向：总量上升是好事，失败上升是坏事
        self.assertTrue(by_key["users_total"]["good_when_up"])
        self.assertFalse(by_key["login_failures_1h"]["good_when_up"])

    def test_summary_single_frame_has_no_delta(self):
        db, _, _ = _fake_db([_result(rows=[_snapshot_row(datetime(2026, 11, 19, 2, 0), {"users_total": 105})])])
        data = _run(oms.latest_summary(db))
        self.assertTrue(data["available"])
        item = next(i for i in data["items"] if i["key"] == "users_total")
        self.assertEqual(item["value"], 105)
        self.assertIsNone(item["previous"])
        self.assertIsNone(item["delta"])

    def test_summary_degrades_when_table_missing(self):
        db, _, _ = _fake_db([])
        db.execute = mock.AsyncMock(side_effect=RuntimeError("table missing"))
        data = _run(oms.latest_summary(db))
        self.assertFalse(data["available"])
        self.assertEqual(data["items"], [])


class OpsMetricsApiTests(unittest.TestCase):
    def test_endpoint_shape_and_options(self):
        from tests.conftest import async_client

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                resp = await client.get(
                    "/api/v1/admin/ops-metrics", params={"metric": "users_total", "hours": 24}
                )
            return resp

        resp = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["metric"], "users_total")
        # 选项与 METRIC_LABELS 同源下发
        option_values = [o["value"] for o in body["data"]["metric_options"]]
        self.assertEqual(option_values, list(oms.METRIC_LABELS.keys()))


if __name__ == "__main__":
    unittest.main()
