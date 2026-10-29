"""Redis 降级日志的限流语义（全项目代码审查 2026-10-10 P2）。

## 为什么需要这个原语

全仓原有 20+ 处 `except Exception: pass` 的 Redis 降级分支 —— **降级行为是对的，
但完全不记日志**，于是「Redis 长期不可用」彻底静默（缓存永不命中、看板数字变 0）。

直接改成 `logger.warning` 又不行：Redis 不可用是**持续性**故障，
逐请求打 warning 会在几分钟内刷满日志（容器日志上限 20MB×5 会被快速吃掉，
反而把其它真正的错误淹掉）。

`app.redis.log_redis_degraded()` 的折中：**同一调用位置每分钟最多一条 warning**。
本文件把这条契约钉死 —— 限流阈值写错（比如误判成每次都记）会让降级日志重新变成刷屏源，
而且不会报错。
"""

import unittest
from unittest import mock

from app import redis as redis_mod


class LogRedisDegradedTests(unittest.TestCase):
    def setUp(self):
        # 模块级状态：每个用例前清空，避免相互串扰
        redis_mod._degraded_log_at.clear()
        patcher = mock.patch.object(redis_mod, "logger")
        self.logger = patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_failure_is_logged(self):
        redis_mod.log_redis_degraded("svc.fn", ConnectionError("down"))
        self.assertEqual(self.logger.warning.call_count, 1)
        message = self.logger.warning.call_args[0][0]
        self.assertIn("svc.fn", message)  # 定位信息必须带在消息里
        self.assertIn("down", message)

    def test_repeated_failures_are_rate_limited(self):
        for _ in range(50):
            redis_mod.log_redis_degraded("svc.fn", ConnectionError("down"))
        self.assertEqual(
            self.logger.warning.call_count,
            1,
            "同一位置在限流窗口内只应记录一条 —— 否则 Redis 宕机时日志会被刷满",
        )

    def test_different_where_are_limited_independently(self):
        redis_mod.log_redis_degraded("svc.a", ConnectionError("down"))
        redis_mod.log_redis_degraded("svc.b", ConnectionError("down"))
        self.assertEqual(
            self.logger.warning.call_count,
            2,
            "限流必须按调用位置分别计算，否则第一个报错的位置会『吃掉』其它位置的告警",
        )

    def test_logs_again_after_window(self):
        redis_mod.log_redis_degraded("svc.fn", ConnectionError("down"))
        self.assertEqual(self.logger.warning.call_count, 1)
        # 把上次记录时间往前推，模拟限流窗口已过（不真的 sleep 60s）
        redis_mod._degraded_log_at["svc.fn"] -= redis_mod._DEGRADED_LOG_INTERVAL + 1
        redis_mod.log_redis_degraded("svc.fn", ConnectionError("down"))
        self.assertEqual(
            self.logger.warning.call_count,
            2,
            "窗口过后必须能再次告警，否则恢复后二次故障将不再可见",
        )

    def test_accepts_plain_string_detail(self):
        """允许传字符串（用于非异常场景的降级说明）。"""
        redis_mod.log_redis_degraded("svc.fn", "缓存值损坏")
        self.assertIn("缓存值损坏", self.logger.warning.call_args[0][0])


class NoSilentRedisDegradationTests(unittest.TestCase):
    """全仓不得再出现「Redis 调用后 except Exception: pass」的静默降级。"""

    def test_no_silent_except_pass_left(self):
        import re
        from pathlib import Path

        app_dir = Path(__file__).resolve().parent.parent / "app"
        offenders: list[str] = []
        for path in app_dir.rglob("*.py"):
            lines = path.read_text(encoding="utf-8").split("\n")
            for idx, line in enumerate(lines):
                if not re.match(r"\s*except Exception", line):
                    continue
                window = "\n".join(lines[idx + 1 : idx + 4])
                if "pass" in window and "log" not in window.lower():
                    offenders.append(f"{path.relative_to(app_dir.parent)}:{idx + 1}")

        self.assertEqual(
            offenders,
            [],
            "以下位置仍是静默吞异常（应改调 log_redis_degraded 或 logger.debug）: " + str(offenders),
        )


if __name__ == "__main__":
    unittest.main()
