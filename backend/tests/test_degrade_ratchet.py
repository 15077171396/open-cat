"""降级策略的棘轮守卫（第五轮审查 MNT-10）。

`except Exception` 本身不是错——问题在于**降级方向靠注释各自决定**，且数量只增不减：
每加一处宽泛捕获，就多一个「失败被吞掉、没人知道」的面。这里用棘轮把总数钉住：
只能减少，不能增加。需要新增捕获时，优先用 `app/core/degrade.py` 的两个显式助手
（`degrade_open` / `degrade_closed`），它们至少会在日志里标明方向。
"""

import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
APP = os.path.join(REPO, 'app')

# 棘轮基线（2026-10-20 现采 132）。**只能下调**：修掉一批就同步改小。
# 130 → 132 的两次增加都属「显式降级点」：content_cache 的列表缓存读/写各一处
# （Redis 不可用必须回落到查库，异常类型不固定，只能宽泛捕获并 log_redis_degraded）。
# 132 → 135：新增 3 处**显式降级点**，都在 `tutorial_node_service` 的章节导航缓存
# （PERF-05）—— 读缓存 / 写缓存 / 失效缓存。方向都是「放行」：
# Redis 不可用时回落到查库、失效失败最多旧 5 分钟，绝不能因为缓存故障让翻章 500。
# 135 → 136：`view_counter._claim_pending` 的 EVAL 认领失败要回落到 hgetall+hdel
# （第二轮复审 C-02）。异常类型不固定（ACL 禁 EVAL 抛 ResponseError、连接断开抛
# ConnectionError），只能宽泛捕获；方向是「放行但已修正」—— 回落路径本身也会校验
# HDEL 删除数，不会重复累加。
# 136 → 144（2026-10-27）：新增「运维状态」聚合与「数据质量巡检」两个**纯观测**模块
# （services/system_status_service.py 7 处、services/data_quality_service.py 1 处）。
# 这 8 处全是**显式降级点且方向一致为「放行」**，且每处都已改用
# `degrade_open(...)` 记日志（可按 `[降级-放行]` 检索）：
#   - 文件系统（日志目录 / 备份目录）读不到 → 该项标「不可见」，其余项照常给结论；
#   - Redis 心跳读写失败 → 任务照常执行（观测失败不得反噬任务）；
#   - 深度探针本身抛异常 → 返回「未知 + 告警」而不是 500；
#   - 单条巡检查询失败 → 该项 count=None 并跳过，不中断整轮。
# 为什么不能收窄：这些调用面对的是任意 IO 故障（OSError / Redis 协议错 / DB 驱动错），
# 收窄类型必然漏掉某类失败，而漏掉的表现是「巡检页整块白掉」，比吞掉更糟。
# 144 → 148（2026-10-27）：新增「转化漏斗与留存」聚合（services/funnel_service.py 4 处）。
# 全部为显式降级点、方向一致为「放行」，且都已改用 `degrade_open(...)`：
#   - 漏斗整块聚合失败 → 全部阶段标「未知」而不是编 0（编 0 会被读成「没人走到这一步」）；
#   - 留存某一队列查询失败 → 该列显示「—」，其余列照常；
#   - 结果缓存读/写失败 → 直接实时聚合（缓存只是加速，不是数据源）。
# 不能收窄类型的理由同上一个批次：面对的是任意 DB/Redis 故障。
# 148 → 151（2026-10-27）：新增「站内搜索词日志」（services/search_log_service.py 3 处）。
# 三处都是**显式降级点、方向为放行**，且都已改用 `degrade_open(...)`：
#   - 写日志失败 → SAVEPOINT 隔离后放行（埋点绝不能打挂搜索）；
#   - 报表查询失败（含「表还没建」）→ 返回 available=False 的空报告，页面显示「尚未采集」；
#   - 保留期清理失败 → 不影响其它定时任务。
#
# 📌 待办（下次动这个文件时做）：目前每次新增「观测/旁路」模块都会推高基线，
# 棘轮正在被稀释。正解是在 `app/core/degrade.py` 加一个**上下文管理器**
# `degrade_guard(event)`，把 `except Exception` 收口到 degrade.py 内部一处，
# 调用方写 `with degrade_guard("x"): ...`。这样基线只 +1 且不再随模块数量增长。
# 151 → 157（2026-10-27，TOTP 批次）：新增 6 处，全部是**显式降级点、方向为放行**：
#   - `core/totp.py` 1 处：密钥损坏等价于「验证码不对」，不能 500；
#   - `services/auth_service.py` 3 处：TOTP 失败计数 / 锁定查询 / 清理，Redis 不可用时
#     跳过锁定（密码这一道门仍在，fail-open 是有意选择）；
#   - 其余 2 处为同批次内的辅助降级（均为「记录后继续」）。
#
# ✅ 157 → 149（2026-10-27 当晚）：已在 `app/core/degrade.py` 落地 `degrade_guard`
# 上下文管理器，把「记录后继续」型降级收口到一处，并转换了 10 个调用点：
#   system_status 3（日志目录 / 备份目录 / 深度探针）
#   data_quality 1（单条巡检 SQL）
#   funnel 2（漏斗聚合 / 留存队列）
#   search_log 3（埋点写入 / 报表查询 / 保留期清理）
#   totp 1（密钥解码）
# 净变化：-10 处调用点 +1 处收口点 = 基线从 157 降到 149。
#
# 📌 **新代码规则**：凡是「失败只记日志、流程继续」的降级，一律写
#     with degrade_guard("模块.动作"): ...
#   不要新写 `except Exception`。这样基线只减不增，棘轮才继续有意义。
#   （Redis 专用降级仍用 `log_redis_degraded`：它带 60s 窗口限流，degrade_guard 没有。）
# 149 → 150（2026-10-27，定时任务可管理批次）：`services/scheduled_job_admin_service.py`
# 的「立即执行一次」入队处 **1 处**，方向是**拒绝**（用 `degrade_closed` 记
# `[降级-拒绝]` 并抛 503）。这一处不能收口到 `degrade_guard`：
# 命令队列写不进去时**必须让调用方拿到失败** —— 假装成功会让管理员以为任务已经手动跑过，
# 而实际上队列里那条命令永远不会有人消费。同理不能用 `log_redis_degraded` 后放行。
# 同批次的「冷却标记」写入已改用 `degrade_guard`（那才是真正的「记录后继续」），
# 因此本批次净增仅 1。
# 150 → 151（2026-10-28，AI 写作配图流水线批次）：`services/ai_writing/image_pipeline.py`
# 的「图像生成失败」处 **1 处**，方向是**拒绝**（用 `degrade_closed` 记
# `[降级-拒绝]` 并抛 503）。Provider 的异常类型随供应商/网络栈而变，不能收窄；
# 也不能用 `degrade_guard` 放行 —— 生成失败必须让调用方拿到失败。
# 同批次的「单张转存失败」已改用 `degrade_guard`（跳过该张、继续其余），
# 因此本批次净增仅 1。
# 151 → 138（2026-10-28，AI 写作 HTTP 接口批次）：`api/admin_ai_writing.py` 的
# SSE 流边界 **1 处**。方向是**拒绝**，且已收口到 `degrade_closed(...)`（记
# `[降级-拒绝]` 并抛 AppException，边界再把它翻译成 `event: error`，code=500、
# retryable=true）：状态码在首帧发出后已无法修改，未知异常只能落成错误事件，否则
# 前端拿到半截文本却不知道失败。不能收窄类型 —— 这里要兜的正是「没想到的异常」；
# 也不能用 `degrade_guard` 放行 —— 失败必须让前端知道（brief 明确要求）。
# 同批次「预占之后的记账失败」改用 `except BaseException` + 归还两张票据后重抛，
# 未新增宽泛捕获。
BROAD_EXCEPT_BASELINE = 138


def _count_broad_except() -> int:
    total = 0
    for root, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for name in files:
            if not name.endswith('.py'):
                continue
            with open(os.path.join(root, name), encoding='utf-8') as fh:
                total += fh.read().count('except Exception')
    return total


class BroadExceptRatchet(unittest.TestCase):
    def test_count_does_not_grow(self):
        current = _count_broad_except()
        self.assertLessEqual(
            current,
            BROAD_EXCEPT_BASELINE,
            f"`except Exception` 从 {BROAD_EXCEPT_BASELINE} 涨到 {current}。"
            "新增降级请用 app/core/degrade.py 的 degrade_open / degrade_closed，"
            "它们会在日志里标明「放行 / 拒绝」；确属必须的宽泛捕获，请把本基线调大并在 PR 说明理由。",
        )

    def test_baseline_is_not_stale(self):
        """基线明显高于实际值说明有人修了但没同步 —— 棘轮会因此变松"""
        current = _count_broad_except()
        self.assertGreaterEqual(
            current,
            BROAD_EXCEPT_BASELINE - 10,
            f"实际只有 {current} 处，基线还是 {BROAD_EXCEPT_BASELINE} —— 请把基线同步调小",
        )


class DegradeHelpers(unittest.TestCase):
    def test_open_does_not_raise(self):
        from app.core.degrade import degrade_open

        degrade_open("test.event", ConnectionError("redis down"))  # 不抛即通过

    def test_closed_raises_with_status(self):
        from app.core.degrade import degrade_closed
        from app.core.exceptions import AppException

        with self.assertRaises(AppException) as ctx:
            degrade_closed("test.event", RuntimeError("db down"), status=503, message="稍后再试")
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(ctx.exception.message, "稍后再试")

    def test_default_or_treats_empty_as_missing(self):
        from app.core.degrade import default_or

        self.assertEqual(default_or(None, "x"), "x")
        self.assertEqual(default_or("", "x"), "x")
        self.assertEqual(default_or(0, "x"), 0)
        self.assertEqual(default_or("v", "x"), "v")


if __name__ == '__main__':
    unittest.main()


class DegradeGuardTests(unittest.TestCase):
    """`degrade_guard` 的语义（新代码统一用它收口「记录后继续」型降级）"""

    def test_swallows_exception_and_logs_direction(self):
        from unittest import mock

        from app.core import degrade as degrade_module

        with mock.patch.object(degrade_module.logger, "warning") as warning:
            with degrade_module.degrade_guard("test.event"):
                raise ValueError("boom")
        self.assertTrue(warning.called, "降级必须留痕，否则失败会静默消失")
        self.assertIn("[降级-放行]", warning.call_args[0][0])

    def test_body_after_the_raising_line_does_not_run(self):
        """异常点之后的赋值不会执行 —— 所以调用方**必须**先给安全的默认值"""
        from app.core.degrade import degrade_guard

        value = "default"
        with degrade_guard("test.event"):
            raise RuntimeError("boom")
            value = "never"  # noqa: F841 刻意不可达
        self.assertEqual(value, "default")

    def test_does_not_swallow_base_exception(self):
        """KeyboardInterrupt / CancelledError 必须继续上抛（吞掉会让服务无法优雅停机）"""
        from app.core.degrade import degrade_guard

        with self.assertRaises(KeyboardInterrupt):
            with degrade_guard("test.event"):
                raise KeyboardInterrupt

    def test_observability_modules_only_keep_redis_specific_catches(self):
        """收口之后，这几个模块里允许保留的 `except Exception` **只能是 Redis 专用降级**
        （紧跟 `log_redis_degraded`，它带 60s 窗口限流）；其余一律应改用 `degrade_guard`。
        """
        import os

        watched = (
            "services/system_status_service.py",
            "services/data_quality_service.py",
            "services/funnel_service.py",
            "services/search_log_service.py",
        )
        offenders = []
        for rel in watched:
            path = os.path.join(APP, rel.replace("/", os.sep))
            lines = open(path, encoding="utf-8").read().split("\n")
            for idx, line in enumerate(lines):
                if "except Exception" not in line:
                    continue
                window = "\n".join(lines[idx + 1 : idx + 3])
                if "log_redis_degraded" not in window:
                    offenders.append(f"{rel}:{idx + 1}")
        self.assertEqual(
            offenders, [], f"这些位置应改用 degrade_guard（Redis 降级才用 log_redis_degraded）: {offenders}"
        )
