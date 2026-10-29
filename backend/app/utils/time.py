"""时区工具 - 统一使用 UTC"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回当前 UTC 时间（timezone-aware）"""
    return datetime.now(timezone.utc)


def parse_iso_to_naive_utc(value: str | None) -> datetime | None:
    """解析前端 `toISOString()` 传来的 ISO 字符串 → **naive UTC**。

    为什么必须换算：库内所有时间列都是 naive UTC，而前端带时区偏移
    （`2026-09-06T16:00:00.000Z` 或 `2026-09-06T00:00:00+08:00`）。把带偏移的值
    直接拿去和库列比较，在**东八区开发环境会差 8 小时** —— 表现为「选了 9 月 6 日，
    却筛出 9 月 5 日 16 点之后的记录」（该约定由 `admin_redeem_codes` 先立，这里收拢成一处）。

    空串/None → None（调用方视为不限）。格式非法抛 `ValueError`，由路由层转 400。
    """
    if value is None or not value.strip():
        return None
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
