"""浏览量计数器（Redis 累加 + 定时落库）的离线单测。

背景（第五轮审查 PERF-02）：详情页原先每次 GET 都 `UPDATE view_count = view_count + 1`，
爬虫与预渲染会把它放大成每秒数十次同行使更新。改造后热路径只做一次 `HINCRBY`，
由调度器每分钟合并写库。这里用桩对象验证三条不变量：

1. Redis 可用时**不写库**，且返回「DB 值 + 待落库增量」（用户看到包含本次的实时值）；
2. Redis 不可用时回退为直接 UPDATE +1（行为与改造前一致，计数不丢）；
3. 落库先认领（HDEL）再 UPDATE 提交，提交失败要把增量回填 Redis（宁可少计不可膨胀）。
"""

import unittest


class _StubRedis:
    """只实现 view_counter 用到的三个命令"""

    def __init__(self, hashes=None, fail=None):
        self.hashes = hashes or {}
        self.fail = fail or set()
        self.calls = []

    async def hincrby(self, key, field, amount):
        self.calls.append(("hincrby", key, field, amount))
        if "hincrby" in self.fail:
            raise ConnectionError("redis down")
        current = int(self.hashes.get(key, {}).get(field, 0))
        new = current + int(amount)
        self.hashes.setdefault(key, {})[field] = str(new)
        return new

    async def hgetall(self, key):
        self.calls.append(("hgetall", key))
        if "hgetall" in self.fail:
            raise ConnectionError("redis down")
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key, *fields):
        self.calls.append(("hdel", key, *fields))
        if "hdel" in self.fail:
            raise ConnectionError("redis down")
        for f in fields:
            self.hashes.get(key, {}).pop(f, None)
        return len(fields)


class _EvalStubRedis(_StubRedis):
    """带 EVAL 的桩：模拟 `_CLAIM_SCRIPT`（原子 HGETALL + HDEL）

    真实 Redis 里这段脚本是串行的，所以桩里也一次做完，不暴露中间态。
    """

    def __init__(self, hashes=None, fail=None):
        super().__init__(hashes=hashes, fail=fail)
        self.eval_calls = 0

    async def eval(self, script, numkeys, key):
        self.eval_calls += 1
        self.calls.append(("eval", key))
        if "eval" in self.fail:
            raise ConnectionError("EVAL not allowed")
        h = self.hashes.get(key, {})
        flat = []
        for field, value in list(h.items()):
            flat.extend([field, value])
        self.hashes[key] = {}
        return flat


class _RecDB:
    """记录执行过的语句与事务动作，不连数据库"""

    def __init__(self, fail_commit=False):
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = fail_commit

    async def execute(self, stmt, params=None):
        # 落库走原生 SQL（带绑定参数），第二个位置参数是参数字典
        self.statements.append((stmt, params))

        class _R:
            rowcount = 1

        return _R()

    async def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("db commit failed")

    async def rollback(self):
        self.rollbacks += 1


class ViewCounterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app.services import view_counter

        self.vc = view_counter
        self._orig = view_counter.redis_client

    def tearDown(self):
        self.vc.redis_client = self._orig

    async def test_register_view_uses_redis_only(self):
        """Redis 正常时：一次 HINCRBY，不写库，返回值含本次浏览"""
        stub = _StubRedis(hashes={"view_pending:tutorial": {"7": "2"}})
        self.vc.redis_client = stub
        db = _RecDB()

        shown = await self.vc.register_view(db, "tutorial", 7, current=100)

        # 已有 2 次待落库 + 本次 1 次 → 展示值 103
        self.assertEqual(shown, 103)
        self.assertEqual(db.statements, [], "Redis 可用时不应写库")
        self.assertEqual([c[0] for c in stub.calls], ["hincrby"])

    async def test_register_view_falls_back_to_db(self):
        """Redis 不可用：回退为直接 UPDATE +1，返回值仍 +1"""
        self.vc.redis_client = _StubRedis(fail={"hincrby"})
        db = _RecDB()

        shown = await self.vc.register_view(db, "tutorial", 7, current=None)

        self.assertEqual(shown, 1)
        self.assertEqual(len(db.statements), 1, "降级路径必须写一次库，否则计数丢失")

    async def test_flush_claims_then_updates(self):
        """落库：先 HDEL 认领，再逐条 UPDATE，最后提交"""
        stub = _StubRedis(hashes={"view_pending:tutorial": {"1": "2", "2": "1"}})
        self.vc.redis_client = stub
        db = _RecDB()

        flushed = await self.vc.flush_pending_views(db)

        self.assertEqual(flushed, 2)
        self.assertEqual(len(db.statements), 2)
        self.assertEqual(db.commits, 1)
        self.assertEqual(stub.hashes["view_pending:tutorial"], {}, "落库后必须清空已认领的增量")
        self.assertIn("hdel", [c[0] for c in stub.calls])

    async def test_flush_restores_on_commit_failure(self):
        """提交失败：回滚并把增量回填，避免这批浏览凭空消失"""
        stub = _StubRedis(hashes={"view_pending:tutorial": {"9": "5"}})
        self.vc.redis_client = stub
        db = _RecDB(fail_commit=True)

        await self.vc.flush_pending_views(db)

        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(stub.hashes["view_pending:tutorial"]["9"], "5", "增量必须回填 Redis")

    async def test_flush_skips_when_redis_unavailable(self):
        """Redis 读不到：本轮跳过，不抛异常、不写库"""
        self.vc.redis_client = _StubRedis(fail={"hgetall"})
        db = _RecDB()

        self.assertEqual(await self.vc.flush_pending_views(db), 0)
        self.assertEqual(db.statements, [])

    async def test_unknown_model_key_is_programming_error(self):
        """模型键写错应当直接暴露（KeyError），而不是静默不计数"""
        self.vc.redis_client = _StubRedis()
        with self.assertRaises(KeyError):
            await self.vc.register_view(_RecDB(), "not-a-model", 1, current=0)


class FlushClaimRaceTests(unittest.IsolatedAsyncioTestCase):
    """落库认领的并发正确性（第二轮复审 C-02）

    原实现 `hgetall` 后 `hdel` 且**不校验返回值**：gunicorn 两个 worker 启动时各 flush
    一次、scheduler 每分钟再一次，窗口重叠时同一批增量被写两遍 → 浏览量翻倍。
    这里钉住三条：原子认领、认领冲突放弃、增量不被并发浏览吃掉。
    """

    def setUp(self):
        from app.services import view_counter

        self.vc = view_counter
        self._orig = view_counter.redis_client

    def tearDown(self):
        self.vc.redis_client = self._orig

    async def test_uses_atomic_eval_when_available(self):
        stub = _EvalStubRedis(hashes={"view_pending:tutorial": {"1": "2", "2": "1"}})
        self.vc.redis_client = stub
        db = _RecDB()

        flushed = await self.vc.flush_pending_views(db)

        self.assertEqual(flushed, 2)
        # 四个模型键各认领一次（空 hash 也走一次 EVAL，成本可忽略）
        self.assertEqual(stub.eval_calls, len(self.vc._VIEW_TABLES), "认领必须走 EVAL 而不是两步走")
        self.assertEqual(stub.hashes["view_pending:tutorial"], {})
        self.assertEqual(db.commits, 1)

    async def test_claim_conflict_writes_nothing(self):
        """另一个进程已先认领（HDEL 实际删除数为 0）→ 本轮必须放弃，否则重复累加"""

        class _LostRaceStub(_StubRedis):
            async def hdel(self, key, *fields):
                self.calls.append(("hdel", key, *fields))
                return 0  # 全被别的进程删掉了

        self.vc.redis_client = _LostRaceStub(hashes={"view_pending:tutorial": {"1": "2"}})
        db = _RecDB()

        flushed = await self.vc.flush_pending_views(db)

        self.assertEqual(flushed, 0)
        self.assertEqual(db.statements, [], "认领失败绝不能写库")
        self.assertEqual(db.commits, 0)

    async def test_eval_failure_falls_back_without_double_count(self):
        """EVAL 不可用时退回两步走，但必须校验 HDEL 删除数"""
        stub = _EvalStubRedis(hashes={"view_pending:tutorial": {"5": "3"}}, fail={"eval"})
        self.vc.redis_client = stub
        db = _RecDB()

        flushed = await self.vc.flush_pending_views(db)

        self.assertEqual(flushed, 1)
        self.assertEqual(len(db.statements), 1)
        self.assertIn("hdel", [c[0] for c in stub.calls])

    async def test_large_backlog_is_committed_in_batches(self):
        """积压上千条时分段提交，避免一个秒级长事务"""
        hashes = {"view_pending:tutorial": {str(i): "1" for i in range(1, 1201)}}
        stub = _EvalStubRedis(hashes=hashes)
        self.vc.redis_client = stub
        db = _RecDB()

        flushed = await self.vc.flush_pending_views(db)

        self.assertEqual(flushed, 1200)
        expected_batches = 1200 // self.vc._FLUSH_BATCH_SIZE + (1 if 1200 % self.vc._FLUSH_BATCH_SIZE else 0)
        self.assertEqual(db.commits, expected_batches)
        self.assertGreater(expected_batches, 1, "断言本身要能证明确实分段了")

    async def test_partial_commit_failure_only_restores_uncommitted(self):
        """第一批已提交、第二批失败：只回填未提交的部分，已提交的不能重复加"""

        class _FailSecondCommitDB(_RecDB):
            async def commit(self):
                self.commits += 1
                if self.commits >= 2:
                    raise RuntimeError("second batch failed")

        hashes = {"view_pending:tutorial": {str(i): "1" for i in range(1, 1201)}}
        stub = _EvalStubRedis(hashes=hashes)
        self.vc.redis_client = stub
        db = _FailSecondCommitDB()

        await self.vc.flush_pending_views(db)

        restored = sum(int(v) for v in stub.hashes["view_pending:tutorial"].values())
        self.assertEqual(restored, 1200 - self.vc._FLUSH_BATCH_SIZE)
        self.assertEqual(db.rollbacks, 1)


if __name__ == '__main__':
    unittest.main()
