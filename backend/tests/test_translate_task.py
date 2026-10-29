"""异步翻译任务的执行体边界（缺口台账 P2-45）。

翻译任务的状态在 Redis（多 worker 可读），但**执行体是接单 worker 本地的
`asyncio.create_task`**。三条只有出事当天才看得见的性质，这里全部转成可失败断言：

1. **无并发上限**：每个任务内部还会 fan-out `_CONCURRENCY=3` 路 AI 计费调用，
   原先管理员连点 N 次就是 3N 路并发 + 3N 份 200KB 文本驻留在 384MB/worker 里。
2. **关停无排收**：worker 被回收/OOM/发版杀掉时没人写终态，Redis 里的
   `pending|running` 一路悬到 TTL(600s)，管理端在这 10 分钟里盯一个不会推进的任务。
   本文件用「取消后状态必须是 failed」钉死这条。
3. **陈旧必须判死**：执行体已经不在了（进程被 kill -9 时连排收都不会跑），
   轮询侧必须靠进度时间戳识别并给出终态，而不是等 TTL。
"""

import asyncio
import json
import time
import unittest
from unittest import mock

from app.config import settings
from app.core.exceptions import AppException
from app.services import translate_service as ts
from tests.conftest import FakeRedis


class _TaskTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeRedis()
        ts._translate_tasks.clear()
        self._patches = [
            mock.patch.object(ts, "redis_client", self.fake),
            mock.patch.object(settings, "DEEPSEEK_API_KEY", "sk-test"),
        ]
        for p in self._patches:
            p.start()

    async def asyncTearDown(self):
        # 先排收再停补丁：反过来会让残留任务在「真 redis_client」上写状态（测试里连不通，
        # 每次白等一个连接超时）
        await ts.shutdown_translate_tasks()
        for p in self._patches:
            p.stop()
        ts._translate_tasks.clear()

    async def _settle(self, seconds: float = 0.05) -> None:
        await asyncio.sleep(seconds)

    async def _stored(self, task_id: str) -> dict | None:
        raw = await self.fake.get(ts._TASK_KEY_FMT.format(task_id=task_id))
        return json.loads(raw) if raw else None


class ConcurrencyCapTests(_TaskTestBase):
    async def test_submissions_beyond_the_cap_are_rejected(self):
        gate = asyncio.Event()

        async def slow(content, fmt="markdown", progress_cb=None):
            await gate.wait()
            return "译文"

        with mock.patch.object(ts, "translate_content", slow):
            first = await ts.submit_translate_task("正文一")
            second = await ts.submit_translate_task("正文二")
            self.assertEqual(len(ts._translate_tasks), ts._MAX_CONCURRENT_TASKS_PER_WORKER)

            with self.assertRaises(AppException) as ctx:
                await ts.submit_translate_task("正文三")
            self.assertEqual(ctx.exception.code, 429)
            # 拒绝必须是「零副作用」的：被拒的那次不能留下半截状态
            self.assertEqual(await self._stored("不存在"), None)

            gate.set()
            await self._settle()

            # 前两个已完成并各自摘掉槽位，只剩刚提交的那个 ——
            # 「只增不减」的计数等于第二次拒绝所有请求，这条就是防它
            third = await ts.submit_translate_task("正文三")
            self.assertTrue(third)
            self.assertNotIn(first, ts._translate_tasks)
            self.assertNotIn(second, ts._translate_tasks)
            self.assertIn(third, ts._translate_tasks)
            self.assertEqual(len(ts._translate_tasks), 1)

    async def test_finished_task_frees_its_slot(self):
        async def quick(content, fmt="markdown", progress_cb=None):
            return "译文"

        with mock.patch.object(ts, "translate_content", quick):
            task_id = await ts.submit_translate_task("正文")
            await self._settle()
            self.assertNotIn(task_id, ts._translate_tasks)
            st = await self._stored(task_id)
            self.assertEqual(st["status"], "completed")
            self.assertEqual(st["content"], "译文")


class ShutdownDrainTests(_TaskTestBase):
    async def test_shutdown_cancels_running_task_and_writes_terminal_state(self):
        started = asyncio.Event()

        async def hanging(content, fmt="markdown", progress_cb=None):
            started.set()
            await asyncio.sleep(60)
            return "永远回不来的译文"

        with mock.patch.object(ts, "translate_content", hanging):
            task_id = await ts.submit_translate_task("正文")
            await started.wait()

            interrupted = await ts.shutdown_translate_tasks()

        self.assertEqual(interrupted, 1)
        self.assertEqual(ts._translate_tasks, {}, "排收后仍留有强引用 → 内存与槽位都不释放")
        st = await self._stored(task_id)
        self.assertEqual(st["status"], "failed", "关停后 Redis 里必须已是终态，而不是悬着的 running")
        self.assertIn("服务正在关闭", st["error"])

    async def test_shutdown_is_noop_when_nothing_is_in_flight(self):
        self.assertEqual(await ts.shutdown_translate_tasks(), 0)

    async def test_completed_task_state_is_not_overwritten_by_shutdown(self):
        """已经跑完的任务不该被排收改写成「中断」—— 那是谎报。"""

        async def quick(content, fmt="markdown", progress_cb=None):
            return "译文"

        with mock.patch.object(ts, "translate_content", quick):
            task_id = await ts.submit_translate_task("正文")
            await self._settle()
            # 任务已结束并从登记表移除，排收不会再碰它
            self.assertEqual(await ts.shutdown_translate_tasks(), 0)
        st = await self._stored(task_id)
        self.assertEqual(st["status"], "completed")


class StaleDetectionTests(_TaskTestBase):
    async def _put(self, task_id: str, state: dict) -> None:
        await self.fake.set(
            ts._TASK_KEY_FMT.format(task_id=task_id),
            json.dumps(state, ensure_ascii=False),
            ex=ts._TASK_TTL_SECONDS,
        )

    async def test_stale_running_task_is_reported_as_failed(self):
        await self._put(
            "zombie",
            {
                "status": "running", "done": 2, "total": 9,
                "content": None, "error": None,
                "updated_at": int(time.time()) - (ts._TASK_STALE_SECONDS + 30),
            },
        )
        st = await ts.get_translate_task("zombie")
        self.assertEqual(st["status"], "failed")
        self.assertTrue(st["stale"])
        self.assertIn("无进度", st["error"])

    async def test_fresh_running_task_is_returned_untouched(self):
        """反向对照：没有这条，上面的陈旧守卫可以靠「逢 running 就判死」恒绿。"""
        await self._put(
            "alive",
            {
                "status": "running", "done": 2, "total": 9,
                "content": None, "error": None,
                "updated_at": int(time.time()),
            },
        )
        st = await ts.get_translate_task("alive")
        self.assertEqual(st["status"], "running")
        self.assertNotIn("stale", st)

    async def test_terminal_states_pass_through(self):
        for status in ("completed", "failed"):
            await self._put(
                f"t-{status}",
                {"status": status, "done": 9, "total": 9, "content": "x", "error": None},
            )
            st = await ts.get_translate_task(f"t-{status}")
            self.assertEqual(st["status"], status, "已终态的任务不能被陈旧判定改写")
            self.assertNotIn("stale", st)


if __name__ == "__main__":
    unittest.main()
