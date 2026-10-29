"""降级策略的显式入口。

背景（第五轮审查 MNT-10）：全仓有 100+ 处 `except Exception`，收尾动作靠注释各自决定，
而且**方向相反**——有的 fail-open（Redis 挂了 AI 限额直接跳过 = 不限量），
有的 fail-safe（读不到配置就保持现状）。新写一个 service 的人无法从代码判断该选哪种，
审计时也数不清哪些失败是被允许的。

本模块不改存量代码（那些都有各自的业务理由），只做两件事：
1. 给**新代码**两个显式命名的选择，逼作者在调用处表态；
2. 统一日志字段，便于按 `[降级-放行]` / `[降级-拒绝]` 检索。

⚠️ 选哪个是**业务判断**，不是技术偏好：
- 读缓存 / 统计 / 非关键旁路 → 放行（`degrade_open`），代价是数据短暂陈旧；
- 鉴权 / 计费 / 配额 / 安全状态 → 拒绝（`degrade_closed`），代价是短暂不可用，
  但绝不能让「读不到」推导出「没有限制」。

守卫：`tests/test_degrade_ratchet.py` 统计 `except Exception` 总数，只许减不许增。
"""

from contextlib import contextmanager
from typing import Any, Iterator, NoReturn

from loguru import logger

from app.core.exceptions import AppException

# 日志前缀：方便在日志里按降级方向筛选（也便于将来接告警规则）
OPEN_TAG = "[降级-放行]"
CLOSED_TAG = "[降级-拒绝]"


def degrade_open(event: str, exc: BaseException | None = None, *, detail: str = "") -> None:
    """降级并**放行**：记录后继续用默认值 / 跳过该步骤。

    :param event: 稳定的检索键，建议 `模块.动作`（如 `ai_quota.reserve`）
    :param detail: 补充上下文；不传则用异常文本
    """
    reason = detail or (f"{type(exc).__name__}: {exc}" if exc else "未提供原因")
    logger.warning(f"{OPEN_TAG} {event} —— {reason}")


def degrade_closed(
    event: str,
    exc: BaseException | None = None,
    *,
    status: int = 503,
    message: str = "服务暂时不可用，请稍后重试",
) -> NoReturn:
    """降级并**拒绝**：记录后抛 AppException，让调用方拿到明确失败。

    用于「读不到就不能假设没问题」的场景（鉴权、配额、安全状态）。
    """
    reason = f"{type(exc).__name__}: {exc}" if exc else "未提供原因"
    logger.error(f"{CLOSED_TAG} {event} —— {reason}")
    raise AppException(code=status, message=message) from exc


def default_or(value: Any, fallback: Any) -> Any:
    """读配置/缓存的常用写法：`None` 与空串都视为「没读到」"""
    if value is None or value == "":
        return fallback
    return value


@contextmanager
def degrade_guard(event: str, *, detail: str = "") -> Iterator[None]:
    """「记录后继续」型降级的**统一收口**（上下文管理器）。

    为什么需要它：`except Exception` 分散在几十个模块里时，`tests/test_degrade_ratchet.py`
    的计数会随模块数量线性增长，棘轮（只许减不许增）就被稀释成噪音。
    把捕获点收口到本函数后，新增模块**不再推高基线**，只需 `with degrade_guard("x"): ...`。

    用法（注意：异常被吞掉后代码继续往下走，所以**必须**先给结果一个安全的默认值）::

        rows = []
        with degrade_guard("report.rows", detail="查询失败则显示空榜"):
            rows = (await db.execute(sql)).all()
        return rows

    ⚠️ 不要用它包住「失败必须让调用方知道」的逻辑（鉴权 / 计费 / 配额）——
    那些应当用 `degrade_closed` 或直接上抛。
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 本函数就是宽泛捕获的唯一收口点
        degrade_open(event, exc, detail=detail)
