"""全站 AI 成本护栏与用量统计：全局日/月熔断（触顶 429）+ 按天累计调用次数与 token 的看板统计。

设计取舍：
- 计数器放 Redis（INCR 先占后判，原子 O(1)），compose 已开 AOF 持久化，重启不丢计数；
- fail-open：Redis 故障时放行并打 WARNING，缓存/配额层故障不应把 AI 功能打成 500；
- 默认阈值 0 = 不限制（升级前后行为一致），看板照常统计供运营决定阈值；
- 「先占后判 + 失败归还」：先判后占在并发下必然能钻过去，先 INCR 占位、超限/失败 DECR 归还。
按模型分别计价：token 除总量外按模型 code 分桶，单价从模型管理的单模型单价取、
未命中回落兜底单价；「未归因」档保证历史数据总额不漏算。
⚠️ trend 的 estimated_cost 按兜底单价 × 总量粗估（today/month 为精确值）。

配置来源：全站日/月上限与兜底单价在「系统设置 → AI 成本护栏」，
单模型单价在「系统 → 模型管理」（ai_models）；未配置的项逐项回退 .env。
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.database import async_session
from app.models.system_setting import SystemSetting
from app.redis import log_redis_degraded, redis_client
from app.services import ai_model_service

# 日粒度键的保留天数：看板默认看 30 天，留 45 天余量。
_DAY_TTL = 45 * 86400
# 月粒度键保留约 13 个月，保证「去年同期」也查得到。
_MONTH_TTL = 400 * 86400

# 管理端看板单次最多回看多少天（防止 ?days=100000 拉爆 Redis 往返）
MAX_SUMMARY_DAYS = 90


# ---------------- 生效配置：后台（设置 + 模型管理）优先，.env 兜底 ----------------
#
# 2026-10-19：上限与单价原先只从 .env 读，运营看着看板的「未配置单价」却无处可填。
# 现在与 AI Provider 凭据同一口径：DB（后台可改）优先、逐项回退环境变量。
# 生产是 gunicorn 2 worker，故覆盖值经 Redis 缓存共享（60s TTL），后台保存时主动失效；
# 进程内不再缓存 —— 避免「改完只有其中一个 worker 生效」这类只能靠现象反推的问题。
_QUOTA_SETTING_KEYS = (
    "ai_global_daily_limit",
    "ai_global_monthly_limit",
    "ai_price_input_per_mtok",
    "ai_price_output_per_mtok",
    "ai_writing_enabled",
    "ai_writing_image_daily_limit",
)
_QUOTA_CFG_CACHE_KEY = "ai_quota:cfg"
_QUOTA_CFG_TTL = 60

# DB 不可用时只告警一次：离线测试与 DB 长故障都不该把日志刷满
_db_unavailable_warned = False
# DB 故障后的退避截止时刻（time.monotonic）。**必须有**：MySQL 不可达时单次建连要等
# 连接超时（约 10s），而 reserve_quota 在每次 AI 调用的热路径上 —— 没有退避就会变成
# 「DB 一挂，所有 AI 调用都卡 10 秒」。退避期内直接用 env 配置（行为与升级前一致）。
_db_retry_after = 0.0
_DB_RETRY_INTERVAL = 60.0


@dataclass(frozen=True)
class QuotaConfig:
    """一次请求内使用的生效配置快照（0 一律表示「不启用」）"""

    daily_limit: int = 0
    monthly_limit: int = 0
    price_input: float = 0.0
    price_output: float = 0.0
    image_daily_limit: int = 0
    writing_enabled: bool = False
    model_prices: dict[str, tuple[float, float]] = field(default_factory=dict)

    def price_for(self, model: str) -> tuple[float, float]:
        """某模型适用的 (输入单价, 输出单价)：先查按模型单列，未命中回落兜底单价。"""
        if model:
            hit = self.model_prices.get(model)
            if hit is not None:
                return hit
        return (self.price_input, self.price_output)

    @property
    def price_configured(self) -> bool:
        """是否存在任何可用单价 —— 决定前端是否展示金额（否则显示「未配置单价」）。"""
        if self.price_input > 0 or self.price_output > 0:
            return True
        return any(i > 0 or o > 0 for i, o in self.model_prices.values())


def _env_quota_config() -> QuotaConfig:
    """环境变量兜底配置（与升级前行为完全一致）"""
    return QuotaConfig(
        daily_limit=max(0, int(settings.AI_GLOBAL_DAILY_LIMIT or 0)),
        monthly_limit=max(0, int(settings.AI_GLOBAL_MONTHLY_LIMIT or 0)),
        price_input=max(0.0, float(settings.AI_PRICE_INPUT_PER_MTOK or 0)),
        price_output=max(0.0, float(settings.AI_PRICE_OUTPUT_PER_MTOK or 0)),
        image_daily_limit=max(0, int(settings.AI_WRITING_IMAGE_DAILY_LIMIT or 0)),
        writing_enabled=bool(settings.AI_WRITING_ENABLED),
        model_prices=_model_prices(),
    )


def _as_non_negative(raw: str | None, default: float) -> float:
    """把后台存的字符串转成非负数；缺失/脏值一律回退默认（配置不该让业务 500）"""
    if raw is None or not str(raw).strip():
        return default
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and number >= 0 else default


def _apply_overrides(
    env: QuotaConfig,
    stored: dict[str, str],
    model_prices: dict[str, tuple[float, float]] | None = None,
) -> QuotaConfig:
    """用后台显式配置逐项覆盖环境变量（未配置的项保持 env 值）"""
    prices = dict(env.model_prices)
    # 模型管理里的显式单价按 code 覆盖 env 的 AI_MODEL_PRICES；
    # 单价为 0 的模型不进表（表示「回退兜底单价」），因此不会误删 env 配置。
    prices.update(model_prices or {})
    if not stored and not model_prices:
        return env
    writing_raw = stored.get("ai_writing_enabled")
    return QuotaConfig(
        daily_limit=int(_as_non_negative(stored.get("ai_global_daily_limit"), env.daily_limit)),
        monthly_limit=int(
            _as_non_negative(stored.get("ai_global_monthly_limit"), env.monthly_limit)
        ),
        price_input=_as_non_negative(stored.get("ai_price_input_per_mtok"), env.price_input),
        price_output=_as_non_negative(stored.get("ai_price_output_per_mtok"), env.price_output),
        image_daily_limit=int(
            _as_non_negative(
                stored.get("ai_writing_image_daily_limit"), env.image_daily_limit
            )
        ),
        # 开关没存过时保持 env 值：缺省 True 的部署不该被「未配置」覆盖成关闭
        writing_enabled=(
            env.writing_enabled
            if writing_raw is None
            else str(writing_raw).strip() in ("1", "true", "True")
        ),
        model_prices=prices,
    )


async def _cache_get_overrides() -> dict | None:
    """读缓存的后台覆盖值；``None`` = 未缓存/不可用（调用方回源查库）"""
    try:
        raw = await redis_client.get(_QUOTA_CFG_CACHE_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def _cache_set_overrides(payload: dict) -> None:
    try:
        await redis_client.set(
            _QUOTA_CFG_CACHE_KEY, json.dumps(payload, ensure_ascii=False), ex=_QUOTA_CFG_TTL
        )
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("ai_usage_service._cache_set_overrides", exc)


def _split_cached_overrides(cached: dict) -> tuple[dict[str, str], dict[str, tuple[float, float]]]:
    """兼容旧缓存格式（直接是 {setting_key: value}）与新格式（含 settings/model_prices）"""
    if "settings" in cached or "model_prices" in cached:
        raw_settings = cached.get("settings")
        settings_part = raw_settings if isinstance(raw_settings, dict) else {}
        raw_prices = cached.get("model_prices")
    else:
        settings_part = cached
        raw_prices = {}
    prices: dict[str, tuple[float, float]] = {}
    if isinstance(raw_prices, dict):
        for code, pair in raw_prices.items():
            try:
                prices[str(code)] = (float(pair[0]), float(pair[1]))
            except (TypeError, ValueError, IndexError):
                continue
    return settings_part, prices


async def _load_overrides() -> tuple[dict[str, str], dict[str, tuple[float, float]]]:
    """后台覆盖值：系统设置 4 项 + 模型管理里的按模型单价。

    ⚠️ DB 失败时**不写缓存**：否则一次抖动会把「无覆盖」固化 60 秒，
    表现为「后台保存了单价但金额一直是空的」。
    """
    global _db_unavailable_warned, _db_retry_after
    cached = await _cache_get_overrides()
    if cached is not None:
        return _split_cached_overrides(cached)
    if time.monotonic() < _db_retry_after:
        return {}, {}
    try:
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(SystemSetting).where(
                        SystemSetting.setting_key.in_(_QUOTA_SETTING_KEYS)
                    )
                )
            ).scalars().all()
            model_prices = await ai_model_service.list_model_prices(session)
    except Exception:
        _db_retry_after = time.monotonic() + _DB_RETRY_INTERVAL
        if not _db_unavailable_warned:
            _db_unavailable_warned = True
            logger.warning("读取 AI 配额配置失败，回退环境变量（本进程不再重复告警）")
        return {}, {}
    _db_retry_after = 0.0
    stored = {row.setting_key: row.value for row in rows if row.value}
    await _cache_set_overrides(
        {
            "settings": stored,
            "model_prices": {
                code: [price[0], price[1]] for code, price in model_prices.items()
            },
        }
    )
    return stored, model_prices


async def get_quota_config() -> QuotaConfig:
    """取生效的 AI 护栏配置：后台配置 > 环境变量。任何异常都退化为 env，绝不抛出。"""
    stored, model_prices = await _load_overrides()
    return _apply_overrides(_env_quota_config(), stored, model_prices)


async def invalidate_quota_config_cache() -> None:
    """后台保存设置后调用：让所有 worker 下次请求回源（TTL 兜底 60s）"""
    try:
        await redis_client.delete(_QUOTA_CFG_CACHE_KEY)
    except Exception:
        log_redis_degraded(
            "ai_usage.invalidate_quota_config",
            "Redis 不可用，AI 护栏配置缓存未主动失效（将随 60s TTL 过期）",
        )


@dataclass(frozen=True)
class QuotaTicket:
    """一次已预占的全局 AI 调用额度；调用失败时用 :func:`release_quota` 归还。"""

    day_calls_key: str
    month_calls_key: str


def _day_stamp(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def _month_stamp(now: datetime) -> str:
    return now.strftime("%Y%m")


def _calls_day_key(now: datetime) -> str:
    return f"ai_usage:calls:day:{_day_stamp(now)}"


def _calls_month_key(now: datetime) -> str:
    return f"ai_usage:calls:month:{_month_stamp(now)}"


def _prompt_day_key(now: datetime) -> str:
    return f"ai_usage:ptok:day:{_day_stamp(now)}"


def _prompt_month_key(now: datetime) -> str:
    return f"ai_usage:ptok:month:{_month_stamp(now)}"


def _completion_day_key(now: datetime) -> str:
    return f"ai_usage:ctok:day:{_day_stamp(now)}"


def _completion_month_key(now: datetime) -> str:
    return f"ai_usage:ctok:month:{_month_stamp(now)}"


def _blocked_day_key(now: datetime) -> str:
    return f"ai_usage:blocked:day:{_day_stamp(now)}"


# 按模型分桶的键（P2-4 遗留：多模型混用时单一单价会算错）

def _model_slug(model: str) -> str:
    """模型名 → Redis 键片段。

    只保留字母数字与 ``. _ -``，其余替换为 ``_`` 并截断：模型名来自配置/代码，
    正常情况下是 ``deepseek`` 这类标识符，但键拼接不该假设输入永远干净
    （含空格或 ``:`` 的名字会把键结构打乱，导致同一个模型被拆成多个桶）。
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)[:40] or "unknown"


def _model_prompt_day_key(now: datetime, model: str) -> str:
    return f"ai_usage:m:{_model_slug(model)}:ptok:day:{_day_stamp(now)}"


def _model_prompt_month_key(now: datetime, model: str) -> str:
    return f"ai_usage:m:{_model_slug(model)}:ptok:month:{_month_stamp(now)}"


def _model_completion_day_key(now: datetime, model: str) -> str:
    return f"ai_usage:m:{_model_slug(model)}:ctok:day:{_day_stamp(now)}"


def _model_completion_month_key(now: datetime, model: str) -> str:
    return f"ai_usage:m:{_model_slug(model)}:ctok:month:{_month_stamp(now)}"


def _models_day_key(now: datetime) -> str:
    """当天出现过用量的模型集合（否则汇总时不知道该去查哪些模型）"""
    return f"ai_usage:models:day:{_day_stamp(now)}"


def _models_month_key(now: datetime) -> str:
    return f"ai_usage:models:month:{_month_stamp(now)}"


# 图片用量与图片费用（Task 6）：张数用整数计数，金额用浮点计数 —— 两者不能共用
# `_safe_mget_ints`，否则 "0.25" 会解析失败并按 0 处理，费用永远显示 0。
#
# 预占与实耗必须分键（Task 6 审查 Critical）：`reserve_image_quota` 只动 imgres，
# `record_image_usage` 只动 img —— 若两者共用一个键，流水线「预占 N → 记录成功 S →
# 归还整张票据 N」会把一次成功记成 2×S（预占 + 实耗），日上限被腰斩、看板翻倍。

def _images_day_key(now: datetime) -> str:
    return f"ai_usage:img:day:{_day_stamp(now)}"


def _image_reservation_day_key(now: datetime) -> str:
    """待出图预占量：只由 reserve/release 增减，不参与实耗统计与看板展示。"""
    return f"ai_usage:imgres:day:{_day_stamp(now)}"


def _images_month_key(now: datetime) -> str:
    return f"ai_usage:img:month:{_month_stamp(now)}"


def _image_cost_day_key(now: datetime) -> str:
    return f"ai_usage:imgcost:day:{_day_stamp(now)}"


def _image_cost_month_key(now: datetime) -> str:
    return f"ai_usage:imgcost:month:{_month_stamp(now)}"


def _images_model_day_key(now: datetime, model: str) -> str:
    return f"ai_usage:imgm:{_model_slug(model)}:day:{_day_stamp(now)}"


def _images_model_month_key(now: datetime, model: str) -> str:
    return f"ai_usage:imgm:{_model_slug(model)}:month:{_month_stamp(now)}"


def _image_model_cost_day_key(now: datetime, model: str) -> str:
    return f"ai_usage:imgmc:{_model_slug(model)}:day:{_day_stamp(now)}"


def _image_model_cost_month_key(now: datetime, model: str) -> str:
    return f"ai_usage:imgmc:{_model_slug(model)}:month:{_month_stamp(now)}"


def _images_models_key(now: datetime, day: bool = True) -> str:
    stamp = _day_stamp(now) if day else _month_stamp(now)
    return f"ai_usage:imgmodels:{'day' if day else 'month'}:{stamp}"


def _to_int(raw) -> int:
    """把 Redis 原始值转 int；None / 非数字都当 0（统计缺数据不该报错，只该显示 0）。"""
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def _safe_mget_ints(keys: list[str]) -> list[int]:
    """批量读多个计数器，语义与 `_to_int` 逐键转换一致，只是把 N 次往返压成 1 次。

    为什么需要它（全项目代码审查 2026-10-10 P2）：
        `get_usage_summary` 的「近 N 天趋势」按天循环、每天 3 次串行 GET，
        而 `days` 上限 90 → 最多 **270 次串行往返**；Redis 与 app 同机部署时，
        串行 RTT 会直接累积成看板响应时间。`_model_breakdown` 同理（每模型 2 次）。

    降级：Redis 故障时返回与 `keys` **等长**的全 0 列表 —— 保证调用方按下标取值安全。
    """
    if not keys:
        return []
    try:
        raw_values = await redis_client.mget(keys)
    except Exception:
        return [0] * len(keys)

    values = [_to_int(raw) for raw in (raw_values or [])]
    if len(values) != len(keys):
        # MGET 理论上返回等长列表；长度异常时补齐/截断，避免调用方下标越界
        values = (values + [0] * len(keys))[: len(keys)]
    return values


async def _safe_mget_floats(keys: list[str]) -> list[float]:
    """批量读浮点计数器（图片费用）。语义同 `_safe_mget_ints`，只是保留小数。"""
    if not keys:
        return []
    raw_values = None
    with degrade_guard("ai_usage.mget_floats", detail="读取图片费用失败，按 0 处理"):
        raw_values = await redis_client.mget(keys)
    if raw_values is None:
        return [0.0] * len(keys)
    values: list[float] = []
    for raw in raw_values or []:
        try:
            values.append(float(raw or 0))
        except (TypeError, ValueError):
            values.append(0.0)
    return (values + [0.0] * len(keys))[: len(keys)]


async def reserve_quota() -> QuotaTicket | None:
    """原子预占一次全局 AI 调用额度。

    返回：
    - :class:`QuotaTicket`：已占用一个名额，调用结束（无论成败）都要归还或确认；
    - ``None``：Redis 不可用 → fail-open 放行，调用方无需归还。

    抛出：
    - :class:`AppException`（429）：已达全局日/月上限。此时**已自动归还**本次占位，
      不会因为「被拒绝的请求」把计数越顶越高。
    """
    now = datetime.now(timezone.utc)
    day_key = _calls_day_key(now)
    month_key = _calls_month_key(now)

    try:
        day_count = await redis_client.incr(day_key)
        if day_count == 1:
            await redis_client.expire(day_key, _DAY_TTL)
        month_count = await redis_client.incr(month_key)
        if month_count == 1:
            await redis_client.expire(month_key, _MONTH_TTL)
    except Exception as exc:
        # 与全项目口径统一：用 log_redis_degraded（同位置每分钟最多一条）
        # 而非每请求一条 warning —— Redis 长故障时后者会刷屏并把真正错误淹掉
        log_redis_degraded("ai_usage_service.reserve_quota", exc)
        return None

    # 生效上限（后台优先、env 兜底）。放在 INCR 之后：Redis 故障时上面已 fail-open 放行，
    # 不该为了一次注定要放行的调用去查库。
    cfg = await get_quota_config()
    daily_limit = cfg.daily_limit
    monthly_limit = cfg.monthly_limit

    exceeded: str | None = None
    if daily_limit > 0 and day_count > daily_limit:
        exceeded = f"今日 AI 调用已达全站上限（{daily_limit} 次/天）"
    elif monthly_limit > 0 and month_count > monthly_limit:
        exceeded = f"本月 AI 调用已达全站上限（{monthly_limit} 次/月）"

    if exceeded is None:
        return QuotaTicket(day_calls_key=day_key, month_calls_key=month_key)

    # 超限：归还本次占位（两个计数器都要还，否则日超限会把月计数越顶越高）
    await release_quota(QuotaTicket(day_calls_key=day_key, month_calls_key=month_key))
    await _mark_blocked(now)
    logger.warning(
        "AI 全局额度已触顶，拒绝调用（日 {} / 上限 {}，月 {} / 上限 {}）",
        day_count - 1,
        daily_limit,
        month_count - 1,
        monthly_limit,
    )
    raise AppException(code=429, message=exceeded + "，请稍后再试或联系管理员")


async def release_quota(ticket: QuotaTicket | None) -> None:
    """归还一次预占额度（AI 调用失败时调用）。``None`` 直接忽略。"""
    if ticket is None:
        return
    try:
        await redis_client.decr(ticket.day_calls_key)
        await redis_client.decr(ticket.month_calls_key)
    except Exception:
        # 归还失败只影响计数精度，绝不能掩盖调用方真正要抛的业务异常
        log_redis_degraded("ai_usage.release_quota", "归还 AI 全局额度计数失败（Redis 不可用）")


async def _mark_blocked(now: datetime) -> None:
    """记录一次「被全局护栏拦下」的调用，供看板观察异常刷量。"""
    key = _blocked_day_key(now)
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, _DAY_TTL)
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("ai_usage_service._mark_blocked", exc)


# 记账熔断（第五轮审查 PERF-11）：
# Redis 被「黑洞」（丢包而非拒绝连接）时，socket 超时 2s/3s，一次 AI 调用返回前
# 要串行等 10 次超时（最坏 20s+）。连续失败 3 次后直接跳过记账 60s，
# 期间只打一条日志 —— 统计宁可少记，也不能让用户在 AI 回答之后还干等 20 秒。
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_SECONDS = 60.0
_fail_streak = 0
_breaker_until = 0.0


def _breaker_open() -> bool:
    return time.monotonic() < _breaker_until


def _is_redis_failure(exc: BaseException) -> bool:
    """区分「Redis 不可用」与「代码/假对象的问题」——只有前者才计入熔断。"""
    if isinstance(exc, (OSError, TimeoutError)):
        return True
    name = type(exc).__name__
    return any(tag in name for tag in ("Connection", "Timeout", "MaxConnections"))


def _note_record_failure() -> None:
    global _fail_streak, _breaker_until
    _fail_streak += 1
    if _fail_streak >= _BREAKER_THRESHOLD and not _breaker_open():
        _breaker_until = time.monotonic() + _BREAKER_COOLDOWN_SECONDS
        logger.warning(
            f"AI 用量记账连续失败 {_fail_streak} 次，熔断 {int(_BREAKER_COOLDOWN_SECONDS)}s（期间跳过记账）"
        )


def _note_record_success() -> None:
    global _fail_streak, _breaker_until
    _fail_streak = 0
    _breaker_until = 0.0


def reset_record_breaker() -> None:
    """重置熔断状态（仅供测试）"""
    global _fail_streak, _breaker_until
    _fail_streak = 0
    _breaker_until = 0.0


async def _safe_smembers(key: str) -> list[str]:
    """读集合成员；Redis 故障或键不存在都返回空列表。"""
    try:
        members = await redis_client.smembers(key)
    except Exception:
        return []
    return sorted(
        m.decode() if isinstance(m, bytes) else str(m) for m in (members or [])
    )


async def record_tokens(
    prompt_tokens: int = 0, completion_tokens: int = 0, model: str = ""
) -> None:
    """累加一次成功调用的 token 用量。任何失败都被吞掉——统计不能影响业务。

    :param model: Provider 名。给了就**同时**写入该模型的分桶与集合
        （总量始终照记，保证「总量 = Σ 各模型 + 未归因」这条等式有据可查）。
    """
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return
    if _breaker_open():
        return

    now = datetime.now(timezone.utc)
    # 一次 pipeline 发完所有计数：原来是 6~10 次串行 RTT（Redis 抖动时逐次累加）
    plan: list[tuple[str, int, int]] = [
        (_prompt_day_key(now), prompt_tokens, _DAY_TTL),
        (_prompt_month_key(now), prompt_tokens, _MONTH_TTL),
        (_completion_day_key(now), completion_tokens, _DAY_TTL),
        (_completion_month_key(now), completion_tokens, _MONTH_TTL),
    ]
    if model:
        plan += [
            (_model_prompt_day_key(now, model), prompt_tokens, _DAY_TTL),
            (_model_prompt_month_key(now, model), prompt_tokens, _MONTH_TTL),
            (_model_completion_day_key(now, model), completion_tokens, _DAY_TTL),
            (_model_completion_month_key(now, model), completion_tokens, _MONTH_TTL),
        ]

    try:
        pipe = redis_client.pipeline()
        for key, amount, ttl in plan:
            if amount <= 0:
                continue
            pipe.incrby(key, amount)
            # 每次写都续 TTL：pipeline 里无法像原来那样判断「是否首次写入」，
            # 而日/月桶的 TTL 从最后一次写入起算完全无害（到期自然消失）
            pipe.expire(key, ttl)
        if model:
            pipe.sadd(_models_day_key(now), model)
            pipe.expire(_models_day_key(now), _DAY_TTL)
            pipe.sadd(_models_month_key(now), model)
            pipe.expire(_models_month_key(now), _MONTH_TTL)
        await pipe.execute()
        _note_record_success()
    except Exception as exc:  # noqa: BLE001 统计失败不能影响业务
        if _is_redis_failure(exc):
            _note_record_failure()
        log_redis_degraded("ai_usage.record_usage", "AI token 用量统计写入失败（Redis 不可用）")


async def record_provider_usage(provider: object, *, usage: dict | None = None) -> None:
    """记账一次 AI 调用的 token 用量。

    :param usage: **推荐**：调用方通过 ``chat(..., usage_out=usage)`` 拿到的字典。
        按引用传递，不依赖读取时机 —— provider 是进程级单例，读它的
        ``last_usage`` 属性只有在「await 返回后立刻读」时才正确
        （第三轮增量审查 P1-44：并发复用同一实例、或把调用包进新 Task 时会被覆盖）。
    :param provider: 仅用于两件事：兼容旧调用（未传 usage 时回退读 ``last_usage``）
        与取 ``provider_name`` 作为模型维度。

    模型名优先取工厂写入的 ``model_key``（模型管理里的稳定 code，旧供应商名即
    种子模型 code，历史数据不断档）；取不到再退回 ``provider_name``；都取不到
    就只记总量 —— 宁可少一个维度，也不能把用量记到错误的模型名下。
    """
    if usage is None:
        # 兼容路径：调用方没传 usage_out，回退读共享属性（存在被并发覆盖的风险）
        usage = getattr(provider, "last_usage", None)
    if not isinstance(usage, dict) or not usage:
        return
    model = getattr(provider, "model_key", "") or getattr(provider, "provider_name", "") or ""
    await record_tokens(
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        model,
    )


# ---------------- 图片配额与写作操作用量（Task 6） ----------------


@dataclass(frozen=True)
class ImageQuotaTicket:
    """一次已预占的图片额度；生成结束后由调用方**整张**归还。"""

    reserve_key: str
    count: int


async def reserve_image_quota(count: int) -> ImageQuotaTicket | None:
    """原子预占 ``count`` 张图片额度（只增加预占键，不动实耗键）。

    判定用「实际已用 + 全部预占」：预占期间图片还没记进实耗，只比实耗会放行超卖；
    成功出图后由调用方 :func:`record_image_usage` 记实耗、并**整张**归还本票据，
    两道计数此消彼长，不会把同一次成功算两遍。

    ``None`` = 未配置日上限或 Redis 不可用（fail-open 放行）；超限抛 429 并
    自动回滚本次预占 —— 与全站配额同一套「先占后判 + 拒绝即归还」。
    """
    cfg = await get_quota_config()
    if cfg.image_daily_limit <= 0:
        return None
    now = datetime.now(timezone.utc)
    usage_key = _images_day_key(now)
    reserve_key = _image_reservation_day_key(now)
    reserved = None
    # 与全站配额同口径：Redis 故障时放行，但必须在日志里标明这是「降级-放行」
    with degrade_guard("ai_usage.reserve_image_quota", detail="Redis 不可用，图片额度放行"):
        reserved = await redis_client.incrby(reserve_key, count)
        if reserved == count:
            await redis_client.expire(reserve_key, _DAY_TTL)
    if reserved is None:
        return None

    # 读「实耗 + 预占」做判定；读取失败与全站配额同口径 fail-open：尽力回滚本次预占
    # 后放行，不能因为读不到计数就把 AI 写作整体打成不可用。
    usage, reservation = 0, reserved
    read_ok = False
    with degrade_guard(
        "ai_usage.reserve_image_quota.read", detail="Redis 不可用，图片额度放行"
    ):
        raw = await redis_client.mget([usage_key, reserve_key]) or []
        usage = _to_int(raw[0] if len(raw) > 0 else None)
        reservation = _to_int(raw[1] if len(raw) > 1 else None)
        read_ok = True
    if not read_ok:
        with degrade_guard(
            "ai_usage.reserve_image_quota.rollback", detail="回滚图片额度预占失败"
        ):
            await redis_client.decrby(reserve_key, count)
        return None

    if usage + reservation <= cfg.image_daily_limit:
        return ImageQuotaTicket(reserve_key=reserve_key, count=count)

    # 超限：只回滚预占键（实耗键本函数从未写过）
    with degrade_guard(
        "ai_usage.reserve_image_quota.rollback", detail="回滚图片额度预占失败"
    ):
        await redis_client.decrby(reserve_key, count)
    raise AppException(code=429, message=f"今日 AI 插图已达上限（{cfg.image_daily_limit} 张/天）")


async def release_image_quota(ticket: ImageQuotaTicket | None, count: int | None = None) -> None:
    """归还图片**预占**额度，默认归还整张票据（``count`` 仅供异常场景部分回滚）。

    成功出图的数量由 :func:`record_image_usage` 单独记入实耗；调用方（Task 8）在
    生成结束后应归还**整张票据**（``release_image_quota(ticket)``），不要只归还
    ``预占数 - 成功数`` —— 预占与实耗是两个键，部分归还会把预占残留到 TTL 过期，
    表现为「额度越用越少、看板却是对的」。
    """
    if ticket is None:
        return
    amount = ticket.count if count is None else max(0, min(count, ticket.count))
    if amount <= 0:
        return
    with degrade_guard("ai_usage.release_image_quota", detail="归还图片额度失败"):
        await redis_client.decrby(ticket.reserve_key, amount)


async def record_image_usage(model: str, count: int, unit_price: float) -> None:
    """记账**成功**出图的张数与费用（按张记账：每张成功图调用一次，``count=1``）。

    只写实耗/费用/按模型键，**不碰预占键**：预占的归还在 :func:`release_image_quota`。
    调用方（``image_pipeline.iter_images``）在发出每张 ``image`` 事件**之前**逐张调用本函数，
    因此 SSE 客户端中途断开也不会出现「文件已转存、额度与费用未记」。同一张图只能记一次
    （既不能逐张记完再整体记一遍）；预占票据仍由调用方**整张**归还，不要按张再减预占。
    """
    if count <= 0:
        return
    now = datetime.now(timezone.utc)
    cost = max(0.0, float(unit_price or 0)) * count
    plan = [
        (_images_day_key(now), count, _DAY_TTL),
        (_images_month_key(now), count, _MONTH_TTL),
        (_images_model_day_key(now, model), count, _DAY_TTL),
        (_images_model_month_key(now, model), count, _MONTH_TTL),
    ]
    with degrade_guard("ai_usage.record_image_usage", detail="图片用量统计写入失败"):
        pipe = redis_client.pipeline()
        for key, amount, ttl in plan:
            pipe.incrby(key, amount)
            pipe.expire(key, ttl)
        if cost > 0:
            pipe.incrbyfloat(_image_cost_day_key(now), cost)
            pipe.expire(_image_cost_day_key(now), _DAY_TTL)
            pipe.incrbyfloat(_image_cost_month_key(now), cost)
            pipe.expire(_image_cost_month_key(now), _MONTH_TTL)
            pipe.incrbyfloat(_image_model_cost_day_key(now, model), cost)
            pipe.expire(_image_model_cost_day_key(now, model), _DAY_TTL)
            pipe.incrbyfloat(_image_model_cost_month_key(now, model), cost)
            pipe.expire(_image_model_cost_month_key(now, model), _MONTH_TTL)
        pipe.sadd(_images_models_key(now, True), model)
        pipe.expire(_images_models_key(now, True), _DAY_TTL)
        pipe.sadd(_images_models_key(now, False), model)
        pipe.expire(_images_models_key(now, False), _MONTH_TTL)
        await pipe.execute()


async def record_writing_operation(operation: str) -> None:
    """累计写作操作次数（按操作分桶），供看板展示实际使用结构。"""
    now = datetime.now(timezone.utc)
    key = f"ai_usage:wop:{_model_slug(operation)}:day:{_day_stamp(now)}"
    count = 0
    with degrade_guard("ai_usage.record_writing_operation", detail="写作操作统计写入失败"):
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, _DAY_TTL)


_WRITING_OPERATIONS = (
    "rewrite", "polish", "expand", "shorten", "continue",
    "summarize", "seo_description", "titles", "keywords", "illustrate_prompt", "illustrate",
)


def estimate_usage_cost(
    prompt_tokens: int, completion_tokens: int, model: str, cfg: QuotaConfig
) -> float | None:
    """按当前单价估算本次调用成本；未配置单价返回 None（前端显示「未配置单价」）。"""
    return _estimate_cost(prompt_tokens, completion_tokens, model, cfg)


# 单价解析（AI_MODEL_PRICES）

# 解析结果缓存：(原始配置串, 解析后的表)。配置运行期不变，避免每次调用都 json.loads。
_price_cache: tuple[str, dict[str, tuple[float, float]]] = ("", {})


def _warn_unknown_models(table: dict[str, tuple[float, float]]) -> None:
    """配置里出现未注册的 Provider 名时打 WARNING。

    这类拼写错误**不会报错**：单价配了但不生效，看板金额悄悄退回全局单价 ——
    正是「配置看起来生效了、其实没有」那一类最难发现的问题。
    """
    try:
        from app.services.ai.factory import registered_provider_names

        known = set(registered_provider_names())
    except Exception:  # pragma: no cover - 仅在导入异常时降级，不阻断配置解析
        return
    unknown = sorted(set(table) - known)
    if unknown:
        logger.warning(
            f"AI_MODEL_PRICES 含未注册的模型名（该条不生效）: {unknown}；"
            f"已注册: {sorted(known)}"
        )


def _parse_model_prices(raw: str) -> dict[str, tuple[float, float]]:
    """解析 ``AI_MODEL_PRICES``；任何异常都降级为「无覆盖」并打日志。

    期望格式：``{"deepseek": {"input": 1.0, "output": 2.0}, "openai": {...}}``
    单位与全局单价一致：元 / 百万 tokens。缺 ``input``/``output`` 按 0 处理。
    """
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.error(f"AI_MODEL_PRICES 不是合法 JSON，已忽略（回退全局单价）: {exc}")
        return {}
    if not isinstance(data, dict):
        logger.error(
            'AI_MODEL_PRICES 必须是对象，形如 {"deepseek": {"input": 1, "output": 2}}'
        )
        return {}

    table: dict[str, tuple[float, float]] = {}
    for model, price in data.items():
        if not isinstance(price, dict):
            logger.error(f"AI_MODEL_PRICES[{model}] 必须是对象，已忽略该条")
            continue
        try:
            in_price = float(price.get("input") or 0)
            out_price = float(price.get("output") or 0)
        except (TypeError, ValueError):
            logger.error(f"AI_MODEL_PRICES[{model}] 的 input/output 必须是数字，已忽略该条")
            continue
        table[str(model)] = (in_price, out_price)
    _warn_unknown_models(table)
    return table


def _model_prices() -> dict[str, tuple[float, float]]:
    global _price_cache
    raw = settings.AI_MODEL_PRICES
    if _price_cache[0] != raw:
        _price_cache = (raw, _parse_model_prices(raw))
    return _price_cache[1]


def _estimate_cost(
    prompt_tokens: int, completion_tokens: int, model: str, cfg: QuotaConfig
) -> float | None:
    """按适用单价估算费用（元）。单价未配置（0）时返回 None，而不是假的 0 元。"""
    in_price, out_price = cfg.price_for(model)
    if in_price <= 0 and out_price <= 0:
        return None
    cost = prompt_tokens / 1_000_000 * in_price + completion_tokens / 1_000_000 * out_price
    return round(cost, 4)


async def _model_breakdown(now: datetime, *, day: bool, cfg: QuotaConfig) -> list[dict]:
    """按模型汇总 token 与费用（按总量降序）。

    先 SMEMBERS 取「本日/本月出现过哪些模型」，再用**一次 MGET** 取回全部模型的
    prompt/completion 计数（此前是每模型 2 次串行 GET）。
    """
    models = await _safe_smembers(_models_day_key(now) if day else _models_month_key(now))
    if not models:
        return []

    if day:
        keys = [
            k
            for model in models
            for k in (_model_prompt_day_key(now, model), _model_completion_day_key(now, model))
        ]
    else:
        keys = [
            k
            for model in models
            for k in (
                _model_prompt_month_key(now, model),
                _model_completion_month_key(now, model),
            )
        ]
    values = await _safe_mget_ints(keys)

    rows: list[dict] = []
    for idx, model in enumerate(models):
        prompt = values[idx * 2]
        completion = values[idx * 2 + 1]
        rows.append(
            {
                "model": model,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "estimated_cost": _estimate_cost(prompt, completion, model, cfg),
            }
        )
    rows.sort(key=lambda r: r["total_tokens"], reverse=True)
    return rows


def _combine_cost(
    by_model: list[dict], total_prompt: int, total_completion: int, cfg: QuotaConfig
) -> tuple[float | None, dict]:
    """总额 = Σ(各模型各自单价) + 未归因(全局单价)。返回 (总额, 未归因明细)。

    未归因 = 总量 − Σ各模型：本次改动之前写入的 token 只存在于总量里，
    直接「总额 = Σ各模型」会把历史数据悄悄算漏。显式列出来才对得上账。
    """
    attributed_prompt = sum(r["prompt_tokens"] for r in by_model)
    attributed_completion = sum(r["completion_tokens"] for r in by_model)
    # max(0, ...) 防 Redis 部分写失败造成的负差额（各模型写成功、总量写失败）
    rest_prompt = max(0, total_prompt - attributed_prompt)
    rest_completion = max(0, total_completion - attributed_completion)
    rest_cost = _estimate_cost(rest_prompt, rest_completion, "", cfg)

    parts = [r["estimated_cost"] for r in by_model if r["estimated_cost"] is not None]
    if rest_cost is not None:
        parts.append(rest_cost)
    total = round(sum(parts), 4) if parts else None
    return total, {
        "prompt_tokens": rest_prompt,
        "completion_tokens": rest_completion,
        "total_tokens": rest_prompt + rest_completion,
        "estimated_cost": rest_cost,
    }


async def get_usage_summary(days: int = 30) -> dict:
    """汇总用量：今日 / 本月 / 近 N 天明细 / 上限与剩余额度。

    纯读操作，Redis 故障时返回全 0 的骨架（看板显示为空而不是报错）。
    """
    days = max(1, min(int(days), MAX_SUMMARY_DAYS))
    now = datetime.now(timezone.utc)
    today = now.date()
    # 生效配置（后台优先、env 兜底）只解析一次，供本次汇总的所有计价/上限判断复用
    cfg = await get_quota_config()

    # 7 个「今日/本月」标量：**一次 MGET**（此前 7 次串行 GET）
    (
        today_calls,
        month_calls,
        today_prompt,
        today_completion,
        month_prompt,
        month_completion,
        today_blocked,
    ) = await _safe_mget_ints(
        [
            _calls_day_key(now),
            _calls_month_key(now),
            _prompt_day_key(now),
            _completion_day_key(now),
            _prompt_month_key(now),
            _completion_month_key(now),
            _blocked_day_key(now),
        ]
    )

    # 按模型分桶（P2-4 遗留）：多模型混用时单一单价会算错，故单价按模型取
    today_by_model = await _model_breakdown(now, day=True, cfg=cfg)
    month_by_model = await _model_breakdown(now, day=False, cfg=cfg)
    today_cost, today_rest = _combine_cost(today_by_model, today_prompt, today_completion, cfg)
    month_cost, month_rest = _combine_cost(month_by_model, month_prompt, month_completion, cfg)

    # 写作操作次数与图片用量：按操作/模型分桶用于结构分析。
    # 成本键是浮点数，必须走 _safe_mget_floats —— int("0.25") 会失败并按 0 处理。
    op_keys = [
        f"ai_usage:wop:{_model_slug(op)}:day:{_day_stamp(now)}"
        for op in _WRITING_OPERATIONS
    ]
    op_counts = await _safe_mget_ints(op_keys)
    by_operation = [
        {"operation": op, "calls": count}
        for op, count in zip(_WRITING_OPERATIONS, op_counts)
        if count > 0
    ]

    images_today, images_month = await _safe_mget_ints([
        _images_day_key(now), _images_month_key(now),
    ])
    img_cost_today, img_cost_month = await _safe_mget_floats([
        _image_cost_day_key(now), _image_cost_month_key(now),
    ])
    # 「有图但没有任何图像模型配了单价」时 image_cost 必须是 null（未配置单价），
    # 不能是 0 —— 0 元在运营眼里等于「插图免费」，与实际账单不符。
    # 只在真的有图时才查模型配置：看板是最常被刷新的只读接口，别为 0 张图多打一次库。
    image_price_configured = True
    if images_today > 0 or images_month > 0:
        image_price_configured = await ai_model_service.has_enabled_image_price()

    images_by_model: list[dict] = []
    image_models = await _safe_smembers(_images_models_key(now, True))
    if image_models:
        image_counts = await _safe_mget_ints([
            _images_model_day_key(now, model) for model in image_models
        ])
        image_costs = await _safe_mget_floats([
            _image_model_cost_day_key(now, model) for model in image_models
        ])
        images_by_model = [
            {
                "model": model,
                "images": image_counts[idx],
                "image_cost": round(image_costs[idx], 4),
            }
            for idx, model in enumerate(image_models)
        ]

    # 近 N 天趋势：先把 3N 个键一次性取回（此前是 N 天 × 3 次串行 GET，最多 270 次）
    trend_days = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    trend_stamps = [
        datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) for day in trend_days
    ]
    trend_values = await _safe_mget_ints(
        [
            key
            for stamp in trend_stamps
            for key in (_calls_day_key(stamp), _prompt_day_key(stamp), _completion_day_key(stamp))
        ]
    )

    daily_trend: list[dict] = []
    for idx, day in enumerate(trend_days):
        calls = trend_values[idx * 3]
        prompt = trend_values[idx * 3 + 1]
        completion = trend_values[idx * 3 + 2]
        daily_trend.append(
            {
                "date": day.isoformat(),
                "calls": calls,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                # 趋势序列按**兜底单价 × 总量**估算（粗估）：逐日逐模型展开需
                # N×M×2 个键，虽可用 MGET 一次取回，但单价也需逐日逐模型重算；
                # 而前端趋势图只用 calls / total_tokens，暂不值得为此增加复杂度。
                "estimated_cost": _estimate_cost(prompt, completion, "", cfg),
            }
        )

    daily_limit = cfg.daily_limit
    monthly_limit = cfg.monthly_limit

    def _remaining(used: int, limit: int) -> int | None:
        if limit <= 0:
            return None  # 未设上限
        return max(0, limit - used)

    def _image_cost(images: int, cost: float) -> float | None:
        """有图但未配置任何图像单价 → None（前端显示未配置单价）；配置了则保留数字。"""
        if images > 0 and not image_price_configured:
            return None
        return round(cost, 4)

    return {
        "today": {
            "calls": today_calls,
            "prompt_tokens": today_prompt,
            "completion_tokens": today_completion,
            "total_tokens": today_prompt + today_completion,
            # 精确值：Σ(各模型 tokens × 各自单价) + 未归因 tokens × 全局单价
            "estimated_cost": today_cost,
            "by_model": today_by_model,
            "unattributed": today_rest,
            "blocked": today_blocked,
            "images": images_today,
            "image_cost": _image_cost(images_today, img_cost_today),
            "images_by_model": images_by_model,
            "by_operation": by_operation,
        },
        "month": {
            "calls": month_calls,
            "prompt_tokens": month_prompt,
            "completion_tokens": month_completion,
            "total_tokens": month_prompt + month_completion,
            "estimated_cost": month_cost,
            "by_model": month_by_model,
            "unattributed": month_rest,
            "images": images_month,
            "image_cost": _image_cost(images_month, img_cost_month),
        },
        "limits": {
            # 0 表示未启用该维度上限（前端据此显示「未限制」而不是「0 次可用」）
            "daily": daily_limit,
            "monthly": monthly_limit,
            "remaining_today": _remaining(today_calls, daily_limit),
            "remaining_month": _remaining(month_calls, monthly_limit),
            "image_daily": cfg.image_daily_limit,
            "remaining_images": _remaining(images_today, cfg.image_daily_limit),
        },
        "pricing": {
            # 兜底单价：未在「按模型单价」里单列的模型都按它计价
            "input_per_mtok": cfg.price_input,
            "output_per_mtok": cfg.price_output,
            # 供前端判断是否展示金额：适用单价全为 0 时 estimated_cost 一律为 null
            "configured": cfg.price_configured,
            # 已单列单价的模型（前端可标注「专属单价」，也便于排查配置没生效）
            "per_model": {
                model: {"input": price[0], "output": price[1]}
                for model, price in cfg.model_prices.items()
            },
        },
        "trend": daily_trend,
    }
