"""重活线程池与默认池的隔离（第四轮全量审查 P1-67）。

`asyncio.to_thread()` 用的是事件循环**默认**线程池，容量 `min(32, cpu+4)` ——
本机 2 核只有 6 条线程，却同时承载 COS `put_object`（同步 SDK，单次最长 30s）、
Pillow WebP 重编码、8MB 页面的 HTML→Markdown，以及 **bcrypt**。
一次管理端 README 导入以 5 路并发传图即可占满 6 中的 5 个，此时 `/auth/login`
的口令校验只能无声排队 —— 现象是「登录卡住」，而限流照常计数、日志一片干净。

本文件钉的是「两条池真的分开」，而不是「代码看着对」。
"""

import asyncio
import threading
import unittest
from unittest import mock

from app.core import executors, security


class HeavyExecutorIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_heavy_runs_on_the_dedicated_pool(self):
        name = await executors.run_heavy(lambda: threading.current_thread().name)
        self.assertTrue(
            name.startswith("heavy-io"),
            f"run_heavy 没走专用池（线程名 {name}）—— 隔离失效，批量传图仍会挤占登录",
        )

    async def test_password_hashing_stays_on_the_default_pool(self):
        """bcrypt 必须留在默认池：两条池互不挤占正是这次改动的目的"""
        seen: dict[str, str] = {}
        real = security.hash_password

        def spy(password: str) -> str:
            seen["thread"] = threading.current_thread().name
            return real(password)

        with mock.patch.object(security, "hash_password", spy):
            await security.hash_password_async("timing-probe")
        self.assertIn("thread", seen)
        self.assertFalse(
            seen["thread"].startswith("heavy-io"),
            f"口令哈希被挪进了重活池（{seen['thread']}）",
        )

    def test_pool_is_bounded(self):
        """无界线程池是这项病因的另一半：必须有上限，且上限要够跑一批图"""
        workers = executors.HEAVY_IO_EXECUTOR._max_workers
        self.assertLessEqual(workers, 16)
        self.assertGreaterEqual(workers, 4)

    async def test_run_heavy_propagates_exceptions(self):
        """线程池包装不能把异常吞成 None（否则上传失败会静默保留原 URL）"""

        def boom():
            raise ValueError("转码失败")

        with self.assertRaises(ValueError):
            await executors.run_heavy(boom)

    async def test_pool_actually_caps_concurrency(self):
        counters = {"active": 0, "peak": 0}

        def job():
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
            try:
                import time

                time.sleep(0.03)
            finally:
                counters["active"] -= 1

        await asyncio.gather(*[executors.run_heavy(job) for _ in range(30)])
        self.assertLessEqual(
            counters["peak"],
            executors.HEAVY_IO_WORKERS,
            f"同时跑了 {counters['peak']} 个重任务，超过池上限 {executors.HEAVY_IO_WORKERS}",
        )


class FetchBudgetRemovedTests(unittest.TestCase):
    """第三方抓取模块（third_party_fetch）已随 0080_drop_commercial_modules 删除。

    原有的「总时间预算」守卫随模块一并移除；AI 调用侧的 AI_TOTAL_BUDGET_SECONDS
    守卫仍在 test_ai_playground / ai_config 相关用例中。
    """

    def test_module_is_gone(self):
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("app.services.third_party_fetch"))


if __name__ == "__main__":
    unittest.main()
