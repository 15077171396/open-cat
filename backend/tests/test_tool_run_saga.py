"""run_tool 三段式事务（saga）回归测试。

背景（PERF/可用性加固）：AI 工具的扣费/时长统计须先于外部 AI HTTP 调用
独立提交（阶段一），AI 调用期间不再持有 users 行锁与连接池连接；
调用失败时通过补偿退还扣费并回退时长统计（阶段三失败路径），
对用户可见行为与旧「单事务整体回滚」等价。

使用桩 Session 记录事件顺序，验证 saga 各阶段编排正确、资金不丢。
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import httpx
from sqlalchemy import Select, Update

from app.config import settings
from app.core.exceptions import AppException, FreeTimeExhaustedError
from app.models.point_transaction import PointTransaction
from app.models.tool import Tool
from app.models.download import ToolUsageLog
from app.models.user import User
from app.services import tool_service as ts
from tests.conftest import FakeRedis


class _ScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _ExecResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class FakeSession:
    """最小 AsyncSession 桩：按顺序记录事件，供 saga 流程断言。

    - Select 语句返回预置的 tool（run_tool 仅有工具加载一处 Select）
    - users 表 UPDATE 依次视为「扣费 / 补偿退还」，rowcount 可注入
    - 其他表 UPDATE 视为 usage_count 自增
    """

    def __init__(self, tool: Tool, charge_rowcount: int = 1):
        self.tool = tool
        self.charge_rowcount = charge_rowcount
        self.events: list[str] = []
        self.users_updates: list[Update] = []
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, stmt, *args, **kwargs):
        if isinstance(stmt, Select):
            return _ScalarResult(self.tool)
        if isinstance(stmt, Update):
            if stmt.table.name == "users":
                self.users_updates.append(stmt)
                is_charge = len(self.users_updates) == 1
                self.events.append("charge_update" if is_charge else "refund_update")
                return _ExecResult(self.charge_rowcount if is_charge else 1)
            self.events.append("tool_count_update")
            return _ExecResult(1)
        raise AssertionError(f"unexpected statement type: {type(stmt)}")

    async def refresh(self, obj, *args, **kwargs):
        self.events.append("refresh")

    async def flush(self):
        self.events.append("flush")

    async def commit(self):
        self.commits += 1
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")

    def add(self, obj):
        self.added.append(obj)
        self.events.append(f"add:{type(obj).__name__}")


def _make_ai_tool(points_cost: int = 10) -> Tool:
    return Tool(
        id=100,
        name="测试AI工具",
        description="desc",
        category="prompt",
        tool_type="ai",
        ai_config={"model": "deepseek", "prompt_template": "把{text}翻译成英文"},
        form_schema={"fields": [{"name": "text"}]},
        usage_count=0,
        is_published=True,
        access_mode="points",
        points_cost=points_cost,
    )


def _make_user(points: int = 100) -> User:
    return User(
        id=1,
        account="saga@example.com",
        role="user",
        points=points,
        free_used_seconds=0,
        # free_started_at 默认注册起 3 小时内 → 未耗尽
        free_started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )


class _FakeProvider:
    def __init__(self, fail: bool):
        self._fail = fail
        self.calls = 0

    async def chat(self, messages, usage_out=None, **kwargs):
        self.calls += 1
        if self._fail:
            raise httpx.ConnectTimeout("simulated upstream timeout")
        if usage_out is not None:
            usage_out.update({"prompt_tokens": 5, "completion_tokens": 7})
        return "AI 结果文本"


class RunToolSagaTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # timer_service 持有独立的 redis_client 引用（check_and_consume 的
        # track 防刷键），须一并替换，否则测试会串扰真实 Redis
        from app.services import timer_service as _timer
        self._timer = _timer
        self._orig_timer_redis = _timer.redis_client

        # ai_usage_service 同样持有独立引用（全站 AI 成本护栏，台账 P1-2）。
        # 不替换它的话，run_tool 里的 reserve_quota() 会去连真实 Redis，
        # 测试既失去隔离又可能长时间挂住。
        from app.services import ai_usage_service as _ai_usage
        self._ai_usage = _ai_usage
        self._orig_ai_usage_redis = _ai_usage.redis_client

        self._orig_redis = ts.redis_client
        self._orig_settings = ts.settings
        self._orig_get_provider = ts.get_provider
        self.redis = FakeRedis()
        ts.redis_client = self.redis
        _timer.redis_client = self.redis
        _ai_usage.redis_client = self.redis
        # AI_RETRY_COUNT=0：失败路径单次尝试，避免测试真实 sleep
        ts.settings = settings.model_copy(update={"AI_RETRY_COUNT": 0})

    async def asyncTearDown(self):
        ts.redis_client = self._orig_redis
        self._timer.redis_client = self._orig_timer_redis
        self._ai_usage.redis_client = self._orig_ai_usage_redis
        ts.settings = self._orig_settings
        ts.get_provider = self._orig_get_provider

    def _patch_provider(self, fail: bool) -> _FakeProvider:
        provider = _FakeProvider(fail)

        # get_provider 现为 async（要读后台配置），桩也必须可 await
        async def _fake_get_provider(model=None):
            return provider

        ts.get_provider = _fake_get_provider
        return provider

    # ---------- AI 工具 · 积分模式 ----------

    async def test_points_success_charge_committed_before_ai_call(self):
        """成功路径：扣费在 AI 调用前已提交（阶段一先行落库，不持锁调外部 HTTP）"""
        provider = self._patch_provider(fail=False)
        tool = _make_ai_tool(points_cost=10)
        user = _make_user(points=100)
        db = FakeSession(tool)

        result = await ts.run_tool(db, user, tool.id, {"text": "你好"})

        self.assertEqual(result["result"], "AI 结果文本")
        self.assertEqual(result["tool_name"], tool.name)
        # 阶段一 commit 必须先于 AI 调用（本次修复的核心断言）
        self.assertLess(db.events.index("commit"), db.events.index("add:ToolUsageLog"))
        self.assertEqual(provider.calls, 1)
        # 补记扣费流水 + 使用日志 + 计数自增，最终再次提交
        txs = [o for o in db.added if isinstance(o, PointTransaction)]
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0].points, -10)
        self.assertEqual(txs[0].type, "consume")
        self.assertEqual(len([o for o in db.added if isinstance(o, ToolUsageLog)]), 1)
        self.assertIn("tool_count_update", db.events)
        self.assertEqual(db.commits, 2)
        # 成功调用每日计数保留（不回退）
        daily_key = [k for k in self.redis._store if k.startswith("ai_daily:")][0]
        self.assertEqual(self.redis._store[daily_key][0], 1)

    async def test_points_ai_failure_refunds_compensation(self):
        """失败路径：AI 调用失败后补偿退还扣费，最终状态等价整体回滚"""
        provider = self._patch_provider(fail=True)
        tool = _make_ai_tool(points_cost=10)
        user = _make_user(points=100)
        db = FakeSession(tool)

        with self.assertRaises(AppException) as ctx:
            await ts.run_tool(db, user, tool.id, {"text": "你好"})
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(provider.calls, 1)

        # 阶段一提交先于 AI 调用，失败后 rollback + 补偿退还 + 提交
        self.assertLess(db.events.index("commit"), db.events.index("rollback"))
        self.assertIn("refund_update", db.events)
        self.assertGreater(db.events.index("refund_update"), db.events.index("rollback"))
        self.assertEqual(db.commits, 2)
        # 未产生使用日志与流水（旧整体回滚语义）
        self.assertEqual(db.added, [])
        # 每日计数预占已回退
        daily_key = [k for k in self.redis._store if k.startswith("ai_daily:")][0]
        self.assertEqual(self.redis._store[daily_key][0], 0)

    async def test_points_insufficient_balance_no_ai_call(self):
        """余额不足：403 且不发起 AI 调用、无补偿（阶段一未提交）"""
        provider = self._patch_provider(fail=False)
        tool = _make_ai_tool(points_cost=10)
        user = _make_user(points=5)
        db = FakeSession(tool, charge_rowcount=0)

        with self.assertRaises(AppException) as ctx:
            await ts.run_tool(db, user, tool.id, {"text": "你好"})
        self.assertEqual(ctx.exception.code, 403)
        self.assertTrue(ctx.exception.data["insufficient_points"])
        self.assertEqual(provider.calls, 0)
        self.assertEqual(db.commits, 0)
        self.assertNotIn("refund_update", db.events)
        self.assertEqual(db.added, [])

    async def test_missing_required_field_refunds_after_phase1(self):
        """缺少必填字段（阶段一已提交后失败）：补偿退还，等价旧回滚语义"""
        self._patch_provider(fail=False)
        tool = _make_ai_tool(points_cost=10)
        tool.form_schema = {"fields": [{"name": "other_field"}]}  # 过滤掉 text 字段
        user = _make_user(points=100)
        db = FakeSession(tool)

        with self.assertRaises(AppException) as ctx:
            await ts.run_tool(db, user, tool.id, {"text": "你好"})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("缺少必填字段", ctx.exception.message)
        self.assertIn("refund_update", db.events)
        self.assertEqual(db.added, [])

    # ---------- AI 工具 · 免费时长模式 ----------

    async def test_auth_mode_stats_committed_and_kept_on_success(self):
        """auth 模式成功：时长统计随阶段一提交，使用日志/计数随后提交"""
        self._patch_provider(fail=False)
        tool = _make_ai_tool()
        tool.access_mode = "auth"
        user = _make_user()
        db = FakeSession(tool)

        await ts.run_tool(db, user, tool.id, {"text": "hi"})

        self.assertEqual(user.free_used_seconds, 10)
        self.assertIn("flush", db.events)
        # 无扣费（auth 模式不扣积分）
        self.assertNotIn("charge_update", db.events)
        self.assertEqual(len([o for o in db.added if isinstance(o, ToolUsageLog)]), 1)
        self.assertEqual(db.commits, 2)

    async def test_auth_mode_ai_failure_reverts_stats(self):
        """auth 模式失败：时长统计被补偿回退（等价旧整体回滚）"""
        self._patch_provider(fail=True)
        tool = _make_ai_tool()
        tool.access_mode = "auth"
        user = _make_user()
        db = FakeSession(tool)

        with self.assertRaises(AppException):
            await ts.run_tool(db, user, tool.id, {"text": "hi"})
        # 统计已回退为阶段一前的快照
        self.assertEqual(user.free_used_seconds, 0)

    async def test_auth_mode_exhausted_keeps_stats_and_raises(self):
        """免费时长耗尽：先保存时长统计再抛出（与旧行为一致，不触发补偿）"""
        self._patch_provider(fail=False)
        tool = _make_ai_tool()
        tool.access_mode = "auth"
        user = _make_user()
        # 注册起超过 3 小时 → 自然时间倒计时已耗尽
        user.free_started_at = datetime.now(timezone.utc) - timedelta(hours=4)
        db = FakeSession(tool)

        with self.assertRaises(FreeTimeExhaustedError):
            await ts.run_tool(db, user, tool.id, {"text": "hi"})
        # 耗尽路径显式 commit 保存统计（旧行为），且不做补偿回退
        self.assertEqual(user.free_used_seconds, 10)
        self.assertEqual(db.commits, 1)
        self.assertNotIn("refund_update", db.events)

    # ---------- 前端计算工具（保持旧单事务语义） ----------

    async def test_frontend_tool_points_single_transaction(self):
        """前端计算工具：扣费+流水+计数单事务提交（无 AI 调用，无阶段拆分）"""
        self._patch_provider(fail=False)
        tool = _make_ai_tool()
        tool.tool_type = "frontend"
        tool.ai_config = None
        user = _make_user(points=100)
        db = FakeSession(tool)

        result = await ts.run_tool(db, user, tool.id, {})

        self.assertIn("前端计算", result["result"])
        txs = [o for o in db.added if isinstance(o, PointTransaction)]
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0].points, -10)
        self.assertIn("tool_count_update", db.events)
        # 单事务：仅最终一次 commit
        self.assertEqual(db.commits, 1)

    async def test_free_tool_no_charge(self):
        """free 模式 AI 工具：不计时、不扣费，仅记录使用日志与计数"""
        self._patch_provider(fail=False)
        tool = _make_ai_tool()
        tool.access_mode = "free"
        user = _make_user(points=100)
        db = FakeSession(tool)

        await ts.run_tool(db, user, tool.id, {"text": "hi"})

        self.assertEqual(user.free_used_seconds, 0)
        self.assertNotIn("charge_update", db.events)
        self.assertNotIn("flush", db.events)
        self.assertEqual(user.points, 100)

    async def test_vip_points_mode_skips_charge(self):
        """VIP 用户积分模式：免扣费，也不产生扣费流水"""
        self._patch_provider(fail=False)
        tool = _make_ai_tool(points_cost=10)
        user = _make_user(points=100)
        user.role = "vip"
        db = FakeSession(tool)

        result = await ts.run_tool(db, user, tool.id, {"text": "hi"})

        self.assertNotIn("charge_update", db.events)
        self.assertEqual(
            len([o for o in db.added if isinstance(o, PointTransaction)]), 0
        )
        self.assertEqual(result["result"], "AI 结果文本")

    # ---------- 全站 AI 成本护栏（台账 P1-2）与用户级限额的交互 ----------

    async def test_user_limit_rejection_releases_global_quota(self):
        """用户级超限被拒时必须归还全站占位——否则被拒请求会一路顶高全站计数"""
        self._patch_provider(fail=False)
        # AI_DAILY_LIMIT_PER_USER=0：任何调用都被用户级限额拦下
        ts.settings = ts.settings.model_copy(update={"AI_DAILY_LIMIT_PER_USER": 0})
        tool = _make_ai_tool()
        user = _make_user(points=100)
        db = FakeSession(tool)

        with self.assertRaises(AppException) as ctx:
            await ts.run_tool(db, user, tool.id, {"text": "hi"})
        self.assertEqual(ctx.exception.code, 429)

        counters = [k for k in self.redis._store if k.startswith("ai_usage:calls:")]
        self.assertTrue(counters, "护栏应当留下日/月计数键")
        for key in counters:
            self.assertEqual(self.redis._store[key][0], 0, f"{key} 未被归还")

    async def test_global_quota_exceeded_blocks_before_ai_call(self):
        """全站额度触顶：429、不发起 AI 调用、不扣费，且计数不被顶高"""
        provider = self._patch_provider(fail=False)
        tool = _make_ai_tool(points_cost=10)
        user = _make_user(points=100)
        db = FakeSession(tool)

        from app.services import ai_usage_service as ai_usage

        day_key = ai_usage._calls_day_key(datetime.now(timezone.utc))
        self.redis._store[day_key] = (999, None)
        settings.AI_GLOBAL_DAILY_LIMIT = 999
        try:
            with self.assertRaises(AppException) as ctx:
                await ts.run_tool(db, user, tool.id, {"text": "hi"})
        finally:
            settings.AI_GLOBAL_DAILY_LIMIT = 0

        self.assertEqual(ctx.exception.code, 429)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(db.commits, 0)
        # 被拒的这一次必须归还：计数停在 999，而不是 1000
        self.assertEqual(self.redis._store[day_key][0], 999)
