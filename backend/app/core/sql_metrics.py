"""SQL 慢查询打点与连接池水位（SQLAlchemy 事件监听，零第三方依赖）。

对标 RuoYi 的「缓存监控 / 连接池监视」三件套里我们缺的那件：性能要有数字才有基线。

- 每条经过引擎 cursor 的语句计时：超过 ``settings.SQL_SLOW_QUERY_MS`` 记 warning
  并累计（系统状态页可见，告警走日志）；阈值 0 = 全部记录（仅调试用）。
- 计时槽按**连接**单槽（同一连接串行执行），异常语句留下的残留最多每连接一条，
  不会随失败次数增长 —— 连接池上限即泄漏上限。
- ``pool_status()`` 暴露池水位（size / checked_in / checked_out / overflow）。
- 快照进系统状态响应（/admin/ops/system-status）时**不进 Redis 缓存字典** ——
  这些是瞬时值，由路由层每次现读（见 admin_ops.system_status）。
"""

from __future__ import annotations

import threading
import time

from loguru import logger
from sqlalchemy import event

from app.config import settings

_lock = threading.Lock()
_stats: dict = {
    "total": 0,       # 经过引擎的语句总数（本次进程生命周期）
    "slow": 0,        # 超阈值语句数
    "last_slow_ms": 0.0,
    "last_slow_sql": "",
}
# conn_id -> (t0, sql)。单槽：同一物理连接串行执行，进入新语句即覆盖旧残留。
_pending: dict[int, tuple[float, str]] = {}

_SQL_LOG_LIMIT = 200


def _shorten(sql: str) -> str:
    sql = " ".join((sql or "").split())
    return sql if len(sql) <= _SQL_LOG_LIMIT else sql[: _SQL_LOG_LIMIT - 1] + "…"


def install_sql_metrics(engine) -> None:
    """在引擎（AsyncEngine 或同步 Engine）上安装监听，幂等；不建连接、不抛异常。"""
    sync_engine = getattr(engine, "sync_engine", engine)
    if getattr(sync_engine, "_sql_metrics_installed", False):
        return
    sync_engine._sql_metrics_installed = True

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        _pending[id(conn)] = (time.perf_counter(), statement)

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        started = _pending.pop(id(conn), None)
        if started is None:
            return
        elapsed_ms = (time.perf_counter() - started[0]) * 1000
        threshold = settings.SQL_SLOW_QUERY_MS
        with _lock:
            _stats["total"] += 1
            if elapsed_ms >= threshold:
                _stats["slow"] += 1
                _stats["last_slow_ms"] = round(elapsed_ms, 1)
                _stats["last_slow_sql"] = _shorten(started[1])
        if elapsed_ms >= threshold:
            logger.warning("慢 SQL({ms:.0f}ms，阈值 {t:.0f}ms): {sql}", ms=elapsed_ms, t=threshold, sql=_shorten(statement))


def sql_metrics_snapshot() -> dict:
    """慢查询计数快照（瞬时值，调用时读；不要放进有 TTL 的缓存响应）。"""
    with _lock:
        data = dict(_stats)
    data["slow_threshold_ms"] = settings.SQL_SLOW_QUERY_MS
    return data


def pool_status(engine) -> dict:
    """连接池水位：size / checked_in / checked_out / overflow / status 文本。

    池实现差异用 getattr + callable 探测（不引入裸 except —— degrade 棘轮约束）；
    方法不存在时置 None，异常由调用方的 degrade_guard 兜底。
    """
    pool = getattr(engine, "sync_engine", engine).pool
    out: dict = {}
    for name, fn_name in (
        ("size", "size"),
        ("checked_in", "checkedin"),
        ("checked_out", "checkedout"),
        ("overflow", "overflow"),
    ):
        fn = getattr(pool, fn_name, None)
        out[name] = int(fn()) if callable(fn) else None
    status_fn = getattr(pool, "status", None)
    if callable(status_fn):
        out["status"] = str(status_fn())
    return out
