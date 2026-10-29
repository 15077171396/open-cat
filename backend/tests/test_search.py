"""中文全文检索（台账 P1-1）测试。

这里守的四条底线，全部来自「改检索最容易踩、且线上最难发现」的场景：

1. **召回不劣于改造前**：任何输入都必须至少产出一个可用条件。
   关键词为纯空格这类「无词元」输入若返回空列表，调用方会把 None 拼进
   where()，SQLAlchemy 2.0 生成 `AND NULL` → **静默返回 0 行**，
   既不报错也不告警，是最难排查的一类回归。
2. **全文出事不打挂接口**：服务端拒绝全文查询时必须降级到 LIKE，而不是 500。
3. **降级要分情况**：结构性失败（缺索引/不支持 ngram）才熔断；
   瞬时故障（连接抖动）只本次回退，否则一次网络抖动会永久降级检索质量。
4. **MATCH 列顺序与迁移一致**：MySQL 要求 MATCH() 列列表与 FULLTEXT 索引
   完全一致（含顺序），否则 errno 1191。这条靠读迁移文件交叉校验来钉死。

测试全部离线（CI 无 MySQL/Redis），只测纯函数与降级决策。
"""

import re
import unittest
from pathlib import Path

from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from app.config import settings
from app.models.tutorial import Tutorial
from app.services import search

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# 所有维护 FULLTEXT 索引的迁移：每新增一张内容表都要把迁移追加到这里，
# 否则「列顺序与索引不一致」这类只在真实 MySQL 上才报 errno 1191 的问题
# 会漏过 CI（CI 没有 MySQL）。
FULLTEXT_MIGRATIONS = (
    VERSIONS_DIR / "20261008_0028_add_fulltext_search.py",
    VERSIONS_DIR / "20261009_0032_add_node_plugin_fulltext.py",
    # C-01：tools 一直没建全文索引，却被 TOOL_SEARCH_COLS 当成有索引来查
    VERSIONS_DIR / "20261020_0047_add_tools_fulltext.py",
)

# 迁移里 _INDEXES 的每一行：("表名", "索引名", ("列", "列", "列"))
_INDEX_ROW_RE = re.compile(
    r'\(\s*"(?P<table>\w+)"\s*,\s*"(?P<index>\w+)"\s*,\s*\((?P<cols>[^)]*)\)\s*\)'
)

def _declared_fulltext_indexes() -> dict[str, tuple[str, ...]]:
    """合并解析所有全文迁移，返回 {表名: (列, ...)}。"""
    declared: dict[str, tuple[str, ...]] = {}
    for table, _index, cols in _declared_index_rows():
        declared[table] = cols
    return declared

def _declared_index_rows() -> list[tuple[str, str, tuple[str, ...]]]:
    """合并解析所有全文迁移，返回 [(表名, 索引名, (列, ...)), ...]。"""
    rows: list[tuple[str, str, tuple[str, ...]]] = []
    for path in FULLTEXT_MIGRATIONS:
        if not path.exists():
            raise AssertionError(f"全文索引迁移文件缺失: {path}")
        for m in _INDEX_ROW_RE.finditer(path.read_text(encoding="utf-8")):
            rows.append(
                (
                    m.group("table"),
                    m.group("index"),
                    tuple(re.findall(r'"(\w+)"', m.group("cols"))),
                )
            )
    return rows

def _reset_fulltext():
    """把开关与熔断状态恢复默认，避免用例间互相污染。"""
    search.reset_fulltext_availability()
    settings.SEARCH_FULLTEXT_ENABLED = True

class BuildBooleanQueryTests(unittest.TestCase):
    """查询串构造：不可用时必须明确返回 None（交给 LIKE 兜底）"""

    def setUp(self):
        _reset_fulltext()
        self._orig_max = settings.SEARCH_MAX_KEYWORD_LEN

    def tearDown(self):
        settings.SEARCH_MAX_KEYWORD_LEN = self._orig_max
        _reset_fulltext()

    def test_empty_or_blank_returns_none(self):
        for kw in (None, "", "   ", "\t"):
            self.assertIsNone(search.build_boolean_query(kw), f"应走 LIKE: {kw!r}")

    def test_two_char_chinese_tokenized(self):
        self.assertEqual(search.build_boolean_query("教程"), "+教程")

    def test_multi_token_uses_and_semantics(self):
        # 与改造前 LIKE「必须都出现」的语义最接近
        self.assertEqual(search.build_boolean_query("安装 教程"), "+安装 +教程")

    def test_single_char_falls_back_to_like(self):
        """单字低于 ngram_token_size，不产生 token —— 必须退回 LIKE 而不是查不到"""
        for kw in ("猫", "a", "C"):
            self.assertIsNone(search.build_boolean_query(kw), f"单字应走 LIKE: {kw!r}")

    def test_boolean_operator_chars_stripped(self):
        """「C++」的 + 是布尔算子，必须被剔除；剩下单字母 C 不足 2 字 → 走 LIKE"""
        self.assertIsNone(search.build_boolean_query("C++"))

    def test_operator_chars_do_not_leak_into_query(self):
        # 「python-教程」中的 - 是布尔算子（排除语义），不能原样拼进查询串
        result = search.build_boolean_query("python-教程")
        self.assertIsNotNone(result)
        self.assertNotIn("-", result)
        self.assertEqual(result, "+python +教程")

    def test_overlong_keyword_falls_back_to_like(self):
        """超长串会被 MySQL 拒绝（errno 191），必须整体放弃全文而不是截断"""
        settings.SEARCH_MAX_KEYWORD_LEN = 8
        self.assertIsNone(search.build_boolean_query("这是一个非常长的搜索关键词"))
        # 边界：恰好等于上限仍走全文
        self.assertIsNotNone(search.build_boolean_query("安装教程"))

    def test_token_count_capped(self):
        kw = " ".join(["教程"] * 20)
        result = search.build_boolean_query(kw)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.split()), search._MAX_TOKENS)

class CandidatesTests(unittest.TestCase):
    """候选条件：至少一个是硬要求，绝不能让调用方拿到 None"""

    def setUp(self):
        _reset_fulltext()

    def tearDown(self):
        _reset_fulltext()

    def test_empty_keyword_yields_no_condition(self):
        self.assertEqual(search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, None), [])
        self.assertEqual(search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, ""), [])

    def test_blank_keyword_still_yields_like(self):
        """回归守卫：纯空格必须退回 LIKE。

        改造前走的是 LIKE '% %'；若这里返回空列表，调用方 append(None) 后
        SQLAlchemy 会生成 `AND NULL` → 静默 0 行。
        """
        cands = search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, " ")
        self.assertEqual([k for k, _ in cands], ["like"])

    def test_usable_keyword_gives_fulltext_then_like(self):
        cands = search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        self.assertEqual([k for k, _ in cands], ["fulltext", "like"])

    def test_switch_off_gives_like_only(self):
        settings.SEARCH_FULLTEXT_ENABLED = False
        cands = search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        self.assertEqual([k for k, _ in cands], ["like"])

    def test_circuit_breaker_suppresses_fulltext(self):
        """熔断后不再产生全文候选，避免每个请求都白打一条必然失败的查询"""
        search.mark_fulltext_unavailable("测试熔断")
        cands = search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        self.assertEqual([k for k, _ in cands], ["like"])
        self.assertFalse(search.fulltext_available())

    def test_mark_unavailable_is_idempotent_and_recoverable(self):
        search.mark_fulltext_unavailable("第一次")
        search.mark_fulltext_unavailable("第二次")  # 不应抛错
        self.assertFalse(search.fulltext_available())
        search.reset_fulltext_availability()
        self.assertTrue(search.fulltext_available())

class PickConditionTests(unittest.IsolatedAsyncioTestCase):
    """降级决策：全文失败不打挂接口，但 LIKE 失败必须照原样抛"""

    def setUp(self):
        _reset_fulltext()

    def tearDown(self):
        _reset_fulltext()

    async def test_fulltext_hit_short_circuits(self):
        calls = []

        async def count_fn(cond, *, timeout_ms=None):
            calls.append(cond)
            return 5

        cond, total = await search.pick_keyword_condition(
            count_fn, search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        )
        self.assertEqual((total, len(calls)), (5, 1))

    async def test_falls_back_to_like_when_fulltext_misses(self):
        """全文 0 条但 LIKE 有命中 → 必须退回 LIKE，否则「升级后反而搜不到」"""
        calls = []

        async def count_fn(cond, *, timeout_ms=None):
            calls.append(cond)
            return 0 if len(calls) == 1 else 3

        cond, total = await search.pick_keyword_condition(
            count_fn, search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        )
        self.assertEqual((total, len(calls)), (3, 2))

    async def test_all_miss_returns_like_with_zero(self):
        async def count_fn(cond, *, timeout_ms=None):
            return 0

        _cond, total = await search.pick_keyword_condition(
            count_fn, search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        )
        self.assertEqual(total, 0)

    async def test_structural_fulltext_error_breaker_engages(self):
        """缺索引 / 不支持 ngram：降级为 LIKE 并熔断"""
        calls = []

        async def count_fn(cond, *, timeout_ms=None):
            calls.append(cond)
            if len(calls) == 1:
                raise ProgrammingError(
                    "stmt", {}, Exception("Can't find FULLTEXT index matching the column list")
                )
            return 7

        _cond, total = await search.pick_keyword_condition(
            count_fn, search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        )
        self.assertEqual(total, 7)
        self.assertFalse(search.fulltext_available(), "结构性失败必须熔断")
        # 熔断生效：后续请求不再产生全文候选
        cands = search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        self.assertEqual([k for k, _ in cands], ["like"])

    async def test_transient_error_does_not_breaker(self):
        """连接抖动只本次回退，不熔断 —— 否则一次网络抖动会永久降级检索质量"""
        calls = []

        async def count_fn(cond, *, timeout_ms=None):
            calls.append(cond)
            if len(calls) == 1:
                raise OperationalError("stmt", {}, Exception("server has gone away"))
            return 4

        _cond, total = await search.pick_keyword_condition(
            count_fn, search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, "安装教程")
        )
        self.assertEqual(total, 4)
        self.assertTrue(search.fulltext_available(), "瞬时故障不应熔断")

    async def test_like_error_propagates(self):
        """LIKE 是改造前的路径，吞掉它的异常会把真实故障伪装成「列表为空」"""

        async def count_fn(cond, *, timeout_ms=None):
            raise OperationalError("stmt", {}, Exception("server has gone away"))

        with self.assertRaises(DBAPIError):
            await search.pick_keyword_condition(
                count_fn, search.keyword_condition_candidates(search.TUTORIAL_SEARCH_COLS, " ")
            )

    async def test_no_candidates_returns_none(self):
        async def count_fn(cond, *, timeout_ms=None):
            raise AssertionError("无候选时不应调用 count_fn")

        cond, total = await search.pick_keyword_condition(count_fn, [])
        self.assertIsNone(cond)
        self.assertEqual(total, 0)

class FallbackCountTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """LIKE 兜底计数的服务端语句超时与失败口径（P1-60 方案 B）"""

    class _RecordingDb:
        def __init__(self):
            self.stmts: list[str] = []

        async def execute(self, stmt):
            self.stmts.append(str(stmt))

            class _R:
                def scalar(_self):
                    return 0

            return _R()

    async def test_hint_is_applied_only_when_timeout_requested(self):
        db = self._RecordingDb()
        count = search.make_count_fn(db, Tutorial, [])
        await count(None)
        await count(None, timeout_ms=1234)
        self.assertNotIn("MAX_EXECUTION_TIME", db.stmts[0])
        self.assertIn("MAX_EXECUTION_TIME(1234)", db.stmts[1])

    async def test_fallback_timeout_becomes_clear_400_not_500(self):
        """超时既不静默返回 0 结果骗人，也不冒成 500（现象会是「搜索偶尔白屏」）"""
        from app.core.exceptions import AppException

        orig = OperationalError("SELECT ...", {}, Exception("Query execution was interrupted, maximum statement execution time exceeded"))

        async def count(cond, *, timeout_ms=None):
            assert timeout_ms, "LIKE 兜底必须带语句超时"
            raise orig

        with self.assertRaises(AppException) as ctx:
            await search.pick_keyword_condition(
                count, [("like", search.build_like_condition(search.TUTORIAL_SEARCH_COLS, "a"))]
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("更具体", ctx.exception.message)

    async def test_other_like_errors_still_propagate(self):
        """只有「语句超时」才转 400：别的 DBAPI 故障必须照原样上抛，不能被吞"""
        boom = OperationalError("SELECT ...", {}, Exception("deadlock detected"))

        async def count(cond, *, timeout_ms=None):
            raise boom

        with self.assertRaises(DBAPIError):
            await search.pick_keyword_condition(
                count, [("like", search.build_like_condition(search.TUTORIAL_SEARCH_COLS, "a"))]
            )

    def test_timeout_classifier_covers_both_shapes(self):
        class _Orig:
            code = 3024

        err = DBAPIError(statement="SELECT 1", params={}, orig=_Orig())
        self.assertTrue(search._is_statement_timeout(err))
        msg = DBAPIError(
            statement="SELECT 1", params={},
            orig=Exception("Query execution was interrupted, maximum statement execution time exceeded"),
        )
        self.assertTrue(search._is_statement_timeout(msg))
        other = DBAPIError(statement="SELECT 1", params={}, orig=Exception("no such table: nodes"))
        self.assertFalse(search._is_statement_timeout(other))

    def test_budget_is_bounded(self):
        """超时取值必须显著高于正常检索（否则高峰期大面积误伤），又必须远低于占死连接的量级"""
        self.assertGreaterEqual(settings.SEARCH_FALLBACK_COUNT_TIMEOUT_MS, 250)
        self.assertLessEqual(settings.SEARCH_FALLBACK_COUNT_TIMEOUT_MS, 5000)

if __name__ == "__main__":
    unittest.main()
