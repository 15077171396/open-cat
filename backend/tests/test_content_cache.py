"""公开内容缓存失效回归（全项目代码审查 2026-10-10 P2）。

## 被钉死的缺陷

首页聚合与列表带着 Redis 缓存，但**管理端改完内容后从不失效这些键** ——
管理员改了教程，首页「热门教程」最长 5 分钟仍显示旧值，而且**不报任何错**
（有 TTL 兜底，所以一直没人发现）。

## 两条契约

1. **行为**：`invalidate_tutorials()` 必须删掉它负责的**全部**键 —— 首页聚合、
   公开统计、以及 `list:tutorials:` 前缀族（`limit` 可变、取值集合不封闭）；
   同时**不得误删**不属于它的键（避免「删过头」变成新问题）。
2. **结构**：管理端内容服务的**每一个写操作**（凡含 `await db.commit()` 的函数）
   都必须调用对应的 `invalidate_*()`。用 AST 静态守卫，防止将来新增写函数时漏掉
   —— 这类「漏调用」不会报错，只会静默陈旧，正是本次缺陷的成因。

全部离线：用 `tests.conftest.FakeRedis` 替换 `content_cache.redis_client`，不连真实 Redis。
"""

import ast
import importlib
import unittest
from pathlib import Path
from unittest import mock

from app.services import content_cache
from tests.conftest import FakeRedis

SERVICES_DIR = Path(__file__).resolve().parent.parent / "app" / "services"

# 管理端内容服务 → 它必须调用的失效函数
_ADMIN_WRITE_SERVICES = {
    "admin_tutorial_service.py": "invalidate_tutorials",
}

# 与缓存无关的键，用于验证「不误删」
_UNRELATED_KEYS = ("categories:tutorial", "user_level:1", "stats:dashboard")


def _await_calls(node: ast.AST) -> set[str]:
    """收集函数体内所有 `await f(...)` 的被调用名（仅限简单名字调用）。"""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Await) and isinstance(sub.value, ast.Call):
            func = sub.value.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _has_commit(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "commit"
        ):
            return True
    return False


class InvalidateBehaviourTests(unittest.IsolatedAsyncioTestCase):
    """失效函数删得全、且不误删。"""

    def setUp(self):
        self.redis = FakeRedis()
        patcher = mock.patch.object(content_cache, "redis_client", self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _seed(self) -> None:
        keys = [
            content_cache.home_aggregate_key(),
            content_cache.public_stats_key(),
            # limit 可变 → 必须整族删掉，只删某一个就是漏
            content_cache.list_cache_key("tutorials", {"page": 1, "size": 8}),
            content_cache.list_cache_key("tutorials", {"page": 1, "size": 20}),
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

    async def test_invalidate_tutorials_deletes_home_and_list_variants(self):
        await self._seed()
        await content_cache.invalidate_tutorials()
        await self._assert_gone(
            content_cache.home_aggregate_key(),
            content_cache.public_stats_key(),
            content_cache.list_cache_key("tutorials", {"page": 1, "size": 8}),
            content_cache.list_cache_key("tutorials", {"page": 1, "size": 20}),
        )
        await self._assert_kept(*_UNRELATED_KEYS)

    async def test_redis_failure_does_not_raise(self):
        """Redis 不可用时失效函数必须**吞掉异常**：缓存失效失败不应让管理端写操作报错。"""
        with mock.patch.object(content_cache, "redis_client", _BrokenRedis()):
            await content_cache.invalidate_tutorials()
            await content_cache.invalidate_home()


class _BrokenRedis:
    """所有命令都抛异常，模拟 Redis 宕机。"""

    async def delete(self, *keys):
        raise ConnectionError("redis down")

    async def get(self, key):
        raise ConnectionError("redis down")

    async def incr(self, key):
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
        self.assertGreaterEqual(total_checked, 3, "写操作总数异常，守卫可能已失效")

    def test_content_cache_keys_are_single_sourced(self):
        """公开侧必须使用 content_cache 的键构造器，不得再写字面量。

        否则「后台删的键」与「公开侧写的键」会各写各的，失效静默失灵。
        """
        offenders: list[str] = []
        literals = ('"home_aggregate"', '"public_stats"')
        for fname in ("tutorial_service.py",):
            src = (SERVICES_DIR / fname).read_text(encoding="utf-8")
            for literal in literals:
                if literal in src:
                    offenders.append(f"{fname} 仍含字面量 {literal}")
        self.assertEqual(offenders, [], "缓存键必须统一由 content_cache 定义: " + "; ".join(offenders))


class ShapeVersionTests(unittest.IsolatedAsyncioTestCase):
    """形状版本位（第五轮全量审查 P1-73）。

    这些键存的是**整份序列化结果**。键名不带形状位时，一次「增删响应字段」的发版
    会让新进程原样返回发版前写入的旧结构 —— 无错误码、无日志，现象只是「上线了但
    没生效」，窗口等于 TTL。带形状位后发版即 miss，旧键交给 TTL 回收。
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


if __name__ == "__main__":
    unittest.main()
