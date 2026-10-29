"""计时服务 - 3小时免费时长管理

规则（2026-08-18 起）：免费 3 小时为注册起的自然时间倒计时，
即 free_started_at 起 FREE_TIME_LIMIT 秒内可访问受保护内容，不在线也计时。
free_used_seconds 保留为「实际学习时长」统计（管理后台展示），不再决定是否耗尽。
VIP/admin 不限时。
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.config import settings
from app.models.user import User
from app.redis import log_redis_degraded, redis_client


def _as_utc(dt: datetime) -> datetime:
    """MySQL DateTime 列返回 naive datetime，统一视为 UTC（与 check_vip_expiry 惯例一致）"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# 兜底告警去重：同一用户只告警一次。
# 不限制的话，一条 free_started_at 为 NULL 的脏数据会让每次请求（/auth/me、时长上报）
# 都刷一条 WARNING，把真正有用的日志淹掉。
_warned_missing_started: set[int] = set()
_WARNED_MISSING_STARTED_MAX = 1000


def _resolve_free_started_at(user: User) -> datetime | None:
    """取「免费时长起点」。

    ⚠️ 时区语义（代码审查 D5 / 台账 P2-7）——两个来源并不同源，必须清楚：

    - ``free_started_at`` 由**应用**写入 ``datetime.now(timezone.utc)``（见 auth_service）；
    - 兜底用的 ``created_at`` 由**数据库**写入（``server_default=func.now()``），
      其语义取决于 **MySQL 会话时区**。

    两者只有在会话时区为 UTC 时才同源。部署约定如此（compose 里 mysql / app 都显式
    ``TZ=UTC``），且该前提已由 ``GET /api/v1/health/deep`` 的会话时区探测**持续校验**——
    非 UTC 会让探活返回 503 并给出 ``MySQLSessionNotUTC``，而不是让时间差静默算错。

    为什么仍保留兜底而不是直接判「未开始」：``created_at`` 在约定成立时是可靠的，
    且迁移 0005 已把存量行回填成 ``free_started_at = created_at``；保留兜底可在
    「迁移未跑 / 手工插数据」时不至于把用户挡在门外。但兜底**必须可观测**，
    所以这里打一条（每用户一次的）WARNING。
    """
    if user.free_started_at is not None:
        return user.free_started_at
    if user.created_at is None:
        return None

    user_id = getattr(user, "id", None)
    if user_id not in _warned_missing_started:
        if len(_warned_missing_started) < _WARNED_MISSING_STARTED_MAX:
            _warned_missing_started.add(user_id)
        logger.warning(
            f"用户 {user_id} 的 free_started_at 为空，已回退 created_at 计算免费时长。"
            "两者仅在 MySQL 会话时区为 UTC 时同源（探活 /api/v1/health/deep 会校验），"
            "非 UTC 时该兜底会产生小时级偏差，请检查数据与数据库时区配置。"
        )
    return user.created_at


def is_vip_effective(user: User) -> bool:
    """VIP 权益是否仍有效（admin 恒真；VIP 过期即时视为普通用户）。

    角色降级仅在登录/刷新 token/定时任务时落库，access token 有效期内
    （默认 120 分钟）user.role 可能仍是过期的 "vip"。所有权益判定点
    （教程访问、时长上报、工具扣费）统一走本函数做即时校验，
    避免「VIP 过期后最长 2 小时仍享付费权益」的滞留窗口。
    """
    if user.role == "admin":
        return True
    if user.role != "vip":
        return False
    if not user.vip_expire_time:
        return True  # 无到期时间的 VIP 视为永久（与 check_vip_expiry 口径一致）
    return _as_utc(user.vip_expire_time) > datetime.now(timezone.utc)


def get_free_time_status(user: User) -> dict:
    """按自然时间计算免费时长状态。

    供 /auth/me 等资料接口、访问校验、时长上报三处复用，
    保证「剩余时长/是否耗尽」口径一致。
    """
    if is_vip_effective(user):
        return {
            "free_used_seconds": user.free_used_seconds,
            "free_remaining_seconds": settings.FREE_TIME_LIMIT,
            "is_exhausted": False,
        }

    # 起点：free_started_at 优先；缺失时回退 created_at（见 _resolve_free_started_at
    # 的时区语义说明——该兜底成立的前提由 /health/deep 的会话时区探测守护）
    started_at = _resolve_free_started_at(user)
    if started_at is None:
        return {
            "free_used_seconds": user.free_used_seconds,
            "free_remaining_seconds": settings.FREE_TIME_LIMIT,
            "is_exhausted": False,
        }

    elapsed = int((datetime.now(timezone.utc) - _as_utc(started_at)).total_seconds())
    remaining = max(0, settings.FREE_TIME_LIMIT - elapsed)
    return {
        "free_used_seconds": user.free_used_seconds,
        "free_remaining_seconds": remaining,
        "is_exhausted": remaining == 0,
    }


async def check_and_consume(user: User, db: AsyncSession, duration: int = 10):
    """上报学习时长。

    free_used_seconds 仅作统计累加（防刷：10 秒内重复上报忽略），
    是否耗尽由 get_free_time_status 的自然时间倒计时决定。
    """
    # 兜底：限制 duration 在 [0, 15]，防御其他调用点遗漏校验
    duration = max(0, min(duration, 15))

    # VIP 和管理员不限时（按到期时间即时判定，防过期 VIP 权益滞留）
    if is_vip_effective(user):
        return {
            "free_used_seconds": user.free_used_seconds,
            "free_remaining_seconds": settings.FREE_TIME_LIMIT,
            "is_exhausted": False,
        }

    # 防刷：10秒内重复上报忽略（重复上报仍返回最新倒计时状态）
    track_key = f"track:{user.id}"
    # SET NX 原子判重：GET+SETEX 存在竞态窗口，并发上报会双双累加统计。
    # Redis 故障时降级放行（fail-open）：防刷标记仅影响统计精度，
    # 不应放大为学习上报/AI 工具接口 500（与 home 缓存降级策略一致）
    try:
        first_report = await redis_client.set(track_key, "1", nx=True, ex=10)
    except Exception:
        first_report = True
        log_redis_degraded("timer.dedup_check", "Redis 不可用，学习时长防刷判重降级（统计可能轻微高估）")
    if first_report:
        # 设置防刷标记 (10秒) + 累加实际学习时长（统计用）
        user.free_used_seconds += duration
        await db.flush()

    status = get_free_time_status(user)
    if status["is_exhausted"]:
        logger.info(
            f"用户 {user.id} 免费时长耗尽 "
            f"(free_started_at={user.free_started_at}, 实际学习 {user.free_used_seconds}s)"
        )
    return status
