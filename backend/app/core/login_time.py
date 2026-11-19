"""登录时段限制（2026-11-19）：全站可配置的允许登录窗口。

## 语义

`LOGIN_TIME_WINDOW` 为空 = 不限制（默认）；非空为 `HH:MM-HH:MM`（北京时间），
支持**跨天窗口**（`22:00-06:00` 表示夜里 22 点到次日 6 点）。窗口外的登录请求
直接 403（挂在验证码校验**之前**：快速失败、不烧验证码额度、不泄露账号存在性），
拒绝同样进登录日志（安全遥测，排查「为什么大家都登不上」时是唯一线索）。

## fail-open（刻意的）

配置**解析失败**（手滑写成 `25:00-6:0` 这类）一律按「不限制」处理 —— 时段
限制是加固项，一条坏配置把全站锁死比不限制严重得多；管理页保存时会提示
格式无效，运行期兜底放行并记日志。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.config import settings
from app.core.exceptions import AppException

# 北京时间：登录时段是运营对「本地工作时间」的描述（与定时任务同一口径）
_CST = timezone(timedelta(hours=8))

_WINDOW_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
# 窗口未配置时的兜底说明文案（拒绝信息里的「允许登录时间：…」）
_WINDOW_TEXT = "HH:MM-HH:MM（北京时间，支持跨天，如 22:00-06:00）"

_parsed_cache: tuple[str, tuple[int, int] | None] | None = None


def parse_window(raw: str | None) -> tuple[int, int] | None:
    """解析时段窗口 → (起始分钟, 结束分钟)；非法返回 None（调用方按不限制处理）。"""
    if not raw or not raw.strip():
        return None
    match = _WINDOW_RE.match(raw.strip())
    if not match:
        return None
    start_h, start_m, end_h, end_m = (int(g) for g in match.groups())
    if start_h > 23 or end_h > 23 or start_m > 59 or end_m > 59:
        return None
    return start_h * 60 + start_m, end_h * 60 + end_m


def window_text() -> str:
    """给用户看的窗口说明（拒绝文案用）。"""
    return settings.LOGIN_TIME_WINDOW.strip() or _WINDOW_TEXT


def _window() -> tuple[int, int] | None:
    """带指纹缓存的解析（避免每请求 parse；配置变了自动重算）。"""
    global _parsed_cache
    raw = settings.LOGIN_TIME_WINDOW or ""
    if _parsed_cache is None or _parsed_cache[0] != raw:
        _parsed_cache = (raw, parse_window(raw))
    return _parsed_cache[1]


def is_login_allowed(now: datetime | None = None) -> bool:
    """当前北京时间是否在允许登录的窗口内（未配置/解析失败 = 允许）。"""
    window = _window()
    if window is None:
        return True
    now = now or datetime.now(_CST)
    minutes = now.hour * 60 + now.minute
    start, end = window
    if start <= end:
        return start <= minutes <= end
    # 跨天窗口（22:00-06:00）：夜里时段或凌晨时段
    return minutes >= start or minutes <= end


def check_login_time(now: datetime | None = None) -> None:
    """登录入口的时段闸门：窗口外抛 403（message 面向用户）。"""
    if not is_login_allowed(now):
        logger.bind(event="login_blocked_by_time").warning("登录请求落在禁用时段")
        raise AppException(
            code=403,
            message=f"当前时段不允许登录（允许登录时间：{window_text()}）",
        )
