"""公开内容缓存失效回归（全项目代码审查 2026-10-10 P2）。

## 被钉死的缺陷

资源 / 节点 / 扩展的公开列表与首页聚合都带 Redis 缓存，但**管理端改完内容后
从不失效这些键** —— 管理员改了资源，首页「热门资源」与栏目页最长 5 分钟仍显示
旧值，而且**不报任何错**（有 TTL 兜底，所以一直没人发现）。

## 两条契约

1. **行为**：每个 `invalidate_*()` 必须删掉它负责的**全部**键 —— 包括
   `limit` 可变、取值集合不封闭的 `resources:hot:{limit}` / `plugins:featured:{limit}`
   前缀族；同时**不得误删**不属于它的键（避免"删过头"变成新问题）。
2. **结构**：管理端 5 个内容服务的**每一个写操作**（凡含 `await db.commit()` 的函数）
   都必须调用对应的 `invalidate_*()`。用 AST 静态守卫，防止将来新增写函数时漏掉
   —— 这类"漏调用"不会报错，只会静默陈旧，正是本次缺陷的成因。

全部离线：用 `tests.conftest.FakeRedis` 替换 `content_cache.redis_client`，不连真实 Redis。
"""

import ast
import importlib
import re
import unittest
from pathlib import Path
from unittest import mock

from app.services import content_cache
from tests.conftest import FakeRedis

SERVICES_DIR = Path(__file__).resolve().parent.parent / "app" / "services"

# 管理端内容服务 → 它必须调用的失效函数
_ADMIN_WRITE_SERVICES = {
    "admin_resource_service.py": "invalidate_resources",
    "admin_node_service.py": "invalidate_nodes",
    "admin_plugin_service.py": "invalidate_plugins",
    "admin_tool_service.py": "invalidate_tools",
    "admin_tutorial_service.py": "invalidate_tutorials",
}

# 与缓存无关的键，用于验证「不误删」
_UNRELATED_KEYS = ("categories:tutorial", "user_level:1", "stats:dashboard")


def _await_calls(node: ast.AST) -> set[str]:
    """收集函数体内所有 `await f(...)` 的被调用名（仅限简单名字调用）。"""
    names: set[str] = set()
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Await)):
            continue
        call = sub.value.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
            names.add(call.func.id)
    return names


def _has_commit(node: ast.AST) -> bool:
    """函数体内是否存在语句级的 `await xxx.commit()`。"""
    return any(
        isinstance(sub, ast.Expr)
        and isinstance(sub.value, ast.Await)
        and isinstance(sub.value.value, ast.Call)
        and isinstance(sub.value.value.func, ast.Attribute)
        and sub.value.value.func.attr == "commit"
        for sub in ast.walk(node)
    )


class InvalidateBehaviourTests(unittest.IsolatedAsyncioTestCase):
    """每个失效函数删得全、且不误删。"""

    def setUp(self):
        self.redis = FakeRedis()
        patcher = mock.patch.object(content_cache, "redis_client", self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _seed(self) -> None:
        keys = [
            content_cache.home_aggregate_key(),
            content_cache.resource_stats_key(),
            content_cache.node_stats_key(),
            # limit 可变 → 必须整族删掉，只删某一个就是漏
            content_cache.resource_hot_key(8),
            content_cache.resource_hot_key(20),
            content_cache.resource_latest_key(5),
            content_cache.resource_latest_key(12),
            content_cache.plugin_featured_key(5),
            content_cache.plugin_featured_key(12),
            *_UNRELATED_KEYS,
        ]
        for key in keys:
            await self.redis.set(key, "cached", ex=300)

    async def _assert_gone(self, *keys: str) -> None:
        for key in keys:
            self.assertFalse(await self.redis.exists(key), f"应被删除但仍在: {key}")

    async def _assert_kept(self, *keys: str) -> None:
        for key in keys:
            self.assertTrue(await self.redis.exists(key), f"不应被删除但已消失: {key}")

    async def test_invalidate_resources_deletes_all_limit_variants(self):
        await self._seed()
        await content_cache.invalidate_resources()
        await self._assert_gone(
            content_cache.resource_stats_key(),
            content_cache.home_aggregate_key(),
            content_cache.resource_hot_key(8),
            content_cache.resource_hot_key(20),  # ← 漏了这个就是「只删了见过的 limit」
            content_cache.resource_latest_key(5),
            content_cache.resource_latest_key(12),
        )
        await self._assert_kept(
            content_cache.node_stats_key(),
            content_cache.plugin_featured_key(12),
            *_UNRELATED_KEYS,
        )

    async def test_invalidate_nodes_deletes_stats_and_home(self):
        await self._seed()
        await content_cache.invalidate_nodes()
        await self._assert_gone(content_cache.node_stats_key(), content_cache.home_aggregate_key())
        await self._assert_kept(
            content_cache.resource_stats_key(),
            content_cache.resource_hot_key(8),
            content_cache.plugin_featured_key(12),
            *_UNRELATED_KEYS,
        )

    async def test_invalidate_plugins_deletes_all_limit_variants(self):
        await self._seed()
        await content_cache.invalidate_plugins()
        await self._assert_gone(
            content_cache.home_aggregate_key(),
            content_cache.plugin_featured_key(5),
            content_cache.plugin_featured_key(12),
        )
        await self._assert_kept(
            content_cache.resource_stats_key(),
            content_cache.node_stats_key(),
            content_cache.resource_hot_key(8),
            *_UNRELATED_KEYS,
        )

    async def test_invalidate_tools_and_tutorials_only_touch_home(self):
        # 工具/教程的列表本身不缓存，只影响首页聚合 —— 多删即为误伤
        await self._seed()
        await content_cache.invalidate_tools()
        await self._assert_gone(content_cache.home_aggregate_key())
        await self._assert_kept(
            content_cache.resource_stats_key(),
            content_cache.node_stats_key(),
            content_cache.resource_hot_key(8),
            content_cache.plugin_featured_key(12),
            *_UNRELATED_KEYS,
        )

        await self._seed()
        await content_cache.invalidate_tutorials()
        await self._assert_gone(content_cache.home_aggregate_key())
        await self._assert_kept(
            content_cache.resource_stats_key(),
            content_cache.node_stats_key(),
            content_cache.resource_hot_key(8),
            content_cache.plugin_featured_key(12),
            *_UNRELATED_KEYS,
        )

    async def test_redis_failure_does_not_raise(self):
        """Redis 不可用时失效函数必须**吞掉异常**：缓存失效失败不应让管理端写操作报错。"""
        with mock.patch.object(content_cache, "redis_client", _BrokenRedis()):
            await content_cache.invalidate_resources()
            await content_cache.invalidate_nodes()
            await content_cache.invalidate_plugins()
            await content_cache.invalidate_tools()
            await content_cache.invalidate_tutorials()
            await content_cache.invalidate_home()


class _BrokenRedis:
    """所有命令都抛异常，模拟 Redis 宕机。"""

    async def delete(self, *keys):
        raise ConnectionError("redis down")

    def scan_iter(self, match=None, count=None):  # pragma: no cover - 不会被迭代
        raise ConnectionError("redis down")


class AdminWriteGuardTests(unittest.TestCase):
    """静态守卫：管理端每个写操作都必须失效对应缓存。"""

    def test_every_admin_write_function_invalidates_cache(self):
        total_checked = 0
        problems: list[str] = []

        for fname, func_name in _ADMIN_WRITE_SERVICES.items():
            path = SERVICES_DIR / fname
            module = importlib.import_module(f"app.services.{fname[:-3]}")
            # 先确认函数确实被导入到该模块命名空间（防止守卫比对了一个不存在的名字）
            self.assertTrue(
                hasattr(module, func_name),
                f"{fname} 未导入 {func_name}，守卫会退化为空断言",
            )

            tree = ast.parse(path.read_text(encoding="utf-8"))
            checked = 0
            for node in tree.body:
                if not isinstance(node, ast.AsyncFunctionDef) or not _has_commit(node):
                    continue
                checked += 1
                total_checked += 1
                if func_name not in _await_calls(node):
                    problems.append(f"{fname}::{node.name}()")

            # 枚举数下限：遍历逻辑一旦失效（如改用 ast.walk 只看顶层），
            # 下面的断言会变成空断言而静默通过（沿用 test_admin_route_guards 的教训）
            self.assertGreaterEqual(
                checked, 3, f"{fname} 只枚举到 {checked} 个写函数，守卫可能已失效"
            )

        self.assertEqual(problems, [], f"以下写操作提交后未失效公开缓存: {problems}")
        # 5 个服务合计至少 21 个写操作（当前实测 22 个）
        self.assertGreaterEqual(total_checked, 21, "写操作总数异常，守卫可能已失效")

    def test_content_cache_keys_are_single_sourced(self):
        """公开侧必须使用 content_cache 的键构造器，不得再写字面量。

        否则「后台删的键」与「公开侧写的键」会各写各的，失效静默失灵。
        """
        offenders: list[str] = []
        literals = (
            '"stats:resources"',
            '"stats:nodes"',
            '"resources:hot:',
            '"resources:latest:',
            '"plugins:featured:',
            '"home_aggregate"',
        )
        for fname in ("resource_service.py", "node_service.py", "plugin_service.py"):
            src = (SERVICES_DIR / fname).read_text(encoding="utf-8")
            for literal in literals:
                if literal in src:
                    offenders.append(f"{fname} 仍含字面量 {literal}")
        self.assertEqual(offenders, [], "缓存键必须统一由 content_cache 定义: " + "; ".join(offenders))


class ShapeVersionTests(unittest.IsolatedAsyncioTestCase):
    """形状版本位（第五轮全量审查 P1-73）。

    这些键存的是**整份序列化结果**。键名不带形状位时，一次「增删响应字段」的发版
    会让新进程原样返回发版前写入的旧结构 —— 无错误码、无日志，现象只是「上线了但
    没生效」，窗口等于 TTL（featured 300s+抖动 / 首页聚合 60s）。带形状位后发版即
    miss，旧键交给 TTL 回收。
    """

    def setUp(self):
        self.redis = FakeRedis()
        patcher = mock.patch.object(content_cache, "redis_client", self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _all_keys(self) -> dict[str, str]:
        return {
            "home_aggregate": content_cache.home_aggregate_key(),
            "public_stats": content_cache.public_stats_key(),
            "resource_stats": content_cache.resource_stats_key(),
            "node_stats": content_cache.node_stats_key(),
            "resource_hot": content_cache.resource_hot_key(8),
            "resource_latest": content_cache.resource_latest_key(8),
            "plugin_featured": content_cache.plugin_featured_key(8),
        }

    def test_every_content_key_carries_shape_version(self):
        suffix = f":v{content_cache.CACHE_SHAPE_VERSION}"
        for name, key in self._all_keys().items():
            self.assertIn(suffix, key, f"{name} 的键 {key} 没有形状位，发版后会读到旧结构")

    def test_bump_moves_every_key(self):
        """版本 +1 之后所有键都必须换名 —— 只要有一个键漏了位，它就会跨形状复用旧值。"""
        original = content_cache.CACHE_SHAPE_VERSION
        before = self._all_keys()
        with mock.patch.object(content_cache, "CACHE_SHAPE_VERSION", original + 1):
            after = self._all_keys()
        for name in before:
            self.assertNotEqual(before[name], after[name], f"{name} 未跟随版本移动")
        self.assertEqual(
            len(set(before.values()) | set(after.values())),
            len(before) * 2,
            "新旧键集合有重叠，说明某个键没把形状位拼进去",
        )

    async def test_invalidate_follows_the_current_shape(self):
        """失效侧与写侧必须同步移动：新版键删得到，旧版键交给 TTL（不能误删成跨形状清库）。"""
        original = content_cache.CACHE_SHAPE_VERSION
        old_key = content_cache.plugin_featured_key(12)
        await self.redis.set(old_key, '{"install_count": 0}', ex=300)

        with mock.patch.object(content_cache, "CACHE_SHAPE_VERSION", original + 1):
            new_key = content_cache.plugin_featured_key(12)
            await self.redis.set(new_key, '{"view_count": 0}', ex=300)
            await content_cache.invalidate_plugins()

        self.assertEqual(await self.redis.exists(old_key), 1, "旧形状键不应被误删（它只是等 TTL 回收）")
        self.assertEqual(await self.redis.exists(new_key), 0, "当前形状的键没删到 → 改了不生效")

    async def test_home_and_stats_keys_are_invalidated_by_name(self):
        """聚合与统计卡是定长键，走 `_delete` 而非前缀扫描：漏一个就是「首页卡片 5 分钟不动」。"""
        await self.redis.set(content_cache.home_aggregate_key(), "{}", ex=60)
        await self.redis.set(content_cache.public_stats_key(), "{}", ex=300)
        await content_cache.invalidate_home()
        self.assertEqual(await self.redis.exists(content_cache.home_aggregate_key()), 0)
        self.assertEqual(await self.redis.exists(content_cache.public_stats_key()), 0)


class NoLiteralKeyGuardTests(unittest.TestCase):
    """静态守卫：承载整份响应的缓存键只能由 content_cache 派生，且不再手写版本位。

    扫描范围只取 `app/api` 与 `app/services` —— `app/core/crypto.py` 里的 `enc:v1:` /
    `enc:v2:` 是**密文格式前缀**（值的形状，不是缓存键），把它算进来是误报。
    """

    SCAN_DIRS = [SERVICES_DIR.parent / "api", SERVICES_DIR]

    def _sources(self):
        for base in self.SCAN_DIRS:
            for path in sorted(base.rglob("*.py")):
                if path.name != "content_cache.py":
                    yield path, path.read_text(encoding="utf-8")

    @staticmethod
    def _string_constants(src: str) -> list[str]:
        """只取代码里的字符串字面量。

        按整篇文本匹配会把**注释**里引用的历史键名也算成违规（本轮实测撞到过一次：
        `api/stats.py` 的注释写着「原先这里写死字面量 "public_stats"」），
        守卫一旦误报，下一次就有人直接放宽整条断言。
        """
        return [
            node.value
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]

    def test_no_bare_cache_key_literals_anywhere(self):
        prefixes = (
            "home_aggregate",
            "public_stats",
            "stats:resources",
            "stats:nodes",
            "plugins:featured:",
            "resources:hot:",
            "resources:latest:",
        )
        offenders: list[str] = []
        for path, src in self._sources():
            for value in self._string_constants(src):
                if value.startswith(prefixes):
                    offenders.append(f"{path.name} 写的是字面量 {value!r}")
        self.assertEqual(offenders, [], "缓存键必须由 content_cache 的构造器派生: " + "; ".join(offenders))

    def test_no_hand_written_shape_version(self):
        """`:v2` 这类手写版本位只允许出现在 content_cache 里（两处idiom会漂移）。"""
        offenders = []
        for path, src in self._sources():
            for value in self._string_constants(src):
                if re.search(r":v\d+", value):
                    offenders.append(f"{path.name} 手写了版本位 {value!r}")
        self.assertEqual(offenders, [], "形状版本位应由 shape_key() 派生，不要手写: " + "; ".join(offenders))


if __name__ == "__main__":
    unittest.main()
