"""数据看板统计服务

时间基准约定：全库 naive 时间戳按 UTC 写入（MySQL func.now()，容器 TZ=UTC
已在 docker-compose 显式锁定），本模块统一用 UTC 计算日界/趋势窗口。
部署变更 MySQL 时区前须先确认存量数据口径，避免日界偏移。
"""

from datetime import datetime, timedelta
from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.time import utcnow
from app.redis import log_redis_degraded, redis_client
from app.models.user import User
from app.models.download import Download, ToolUsageLog
from app.models.auth_request import AuthRequest
from app.models.feedback import Feedback
from app.models.tutorial import Tutorial
from app.models.resource import Resource
from app.models.node import Node, Plugin
from app.models.tool import Tool
from app.core.legacy_metrics import legacy_usage_snapshot
from app.services.content_cache import shape_key
from app.config import settings
import json

# 看板缓存键带结构版本号（第五轮全量审查 P1-73 收口）。
# 缓存值是按「当前返回结构」序列化的 JSON，读回时直接原样返回；一旦新增/改名返回字段，旧键里那份
# 不完整的 JSON 会继续被返回，直到 900s TTL 到期或 Redis 重启 —— 表现为「代码已改、接口仍缺字段」，
# 且用 dump.rdb 恢复的实例会把这个问题带到下一次部署之后。
# 这里原先是手写 `"dashboard_stats:v2"`，注释还写着「改字段就把版本号 +1」—— 同一条约定在 content_cache
# 里却完全没有执行（featured / 首页聚合 / 统计卡都是裸键名），于是「记得的那处安全、忘记的那处静默陈旧」。
# 现收进 shape_key()，键名不变（仍是 `dashboard_stats:v2`），但版本位改由 content_cache 的
# CACHE_SHAPE_VERSION 单点决定。
_DASHBOARD_CACHE_KEY = shape_key("dashboard_stats")


async def get_dashboard_stats(db: AsyncSession) -> dict:
    """获取数据看板统计"""
    # 检查缓存（Redis 故障降级直查 DB，缓存层故障不应放大为看板 500）
    cache_key = _DASHBOARD_CACHE_KEY
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        log_redis_degraded("stats.dashboard_cache", "Redis 不可用，数据看板缓存降级直查 DB")

    # 统一使用 UTC
    today = utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())

    # 今日新增注册
    today_registrations = (
        await db.execute(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )
    ).scalar()

    # 今日资源下载量
    today_downloads = (
        await db.execute(
            select(func.count(Download.id)).where(Download.created_at >= today_start)
        )
    ).scalar()

    # 3小时体验转化率
    # BUG 修复（口径）：2026-08-18 起「耗尽」由 free_started_at 自然时间倒计时决定，
    # free_used_seconds 只是实际学习秒数统计（含 10 秒防刷去重），按旧口径
    # `free_used_seconds >= FREE_TIME_LIMIT` 统计耗尽用户，分母趋近 0，
    # 转化率恒为 0 或严重失真。改为按「免费窗口已过」的自然时间口径。
    exhausted_cutoff = (
        utcnow().replace(tzinfo=None) - timedelta(seconds=settings.FREE_TIME_LIMIT)
    )
    total_exhausted = (
        await db.execute(
            select(func.count(User.id)).where(
                User.free_started_at.isnot(None),
                User.free_started_at < exhausted_cutoff,
            )
        )
    ).scalar()
    total_vip = (
        await db.execute(select(func.count(User.id)).where(User.role == "vip"))
    ).scalar()
    conversion_rate = round(total_vip / total_exhausted * 100, 1) if total_exhausted > 0 else 0

    # 知识星球认证数
    approved_count = (
        await db.execute(
            select(func.count(AuthRequest.id)).where(AuthRequest.status == "approved")
        )
    ).scalar()

    # 下载趋势 (近7天) - 单次 GROUP BY 查询替代 7 次循环查询
    trend_start = datetime.combine(today - timedelta(days=6), datetime.min.time())
    trend_rows = (
        await db.execute(
            select(
                func.date(Download.created_at).label("day"),
                func.count(Download.id).label("count"),
            )
            .where(Download.created_at >= trend_start)
            .group_by(func.date(Download.created_at))
        )
    ).all()
    trend_map = {str(row.day): row.count for row in trend_rows}
    trend = [
        {"date": (today - timedelta(days=i)).isoformat(), "count": trend_map.get((today - timedelta(days=i)).isoformat(), 0)}
        for i in range(6, -1, -1)
    ]

    # 注册来源占比 + 用户总数：单条聚合查询合并统计。LIKE '%@%' 前导通配无法命中索引，原先
    # phone/email/total 三条串行全表扫描合并为一次扫描（SUM(CASE)），配合下方 900s 缓存兜底。
    # 口径（第四轮全量审查 P2-34 统一）：手机号=以1开头且不含@；邮箱=含@；**三者都排除软删除用户**。
    # 此前本函数不过滤而 `get_public_stats` 过滤，于是任何一次注销都会让「管理端看板用户数 ≠
    # 首页卡片用户数」，而这里的注释还写着「与其他用户统计口径一致」—— 反向误导。
    # 注销账号不该再算进用户总数：它同时是转化率的分母，虚高会让看板结论偏乐观。
    source_row = (
        await db.execute(
            select(
                func.count(User.id).label("total"),
                func.sum(case((User.account.like("%@%"), 1), else_=0)).label("email"),
                func.sum(
                    case(
                        (and_(User.account.like("1%"), ~User.account.like("%@%")), 1),
                        else_=0,
                    )
                ).label("phone"),
            ).where(User.deleted_at.is_(None))
        )
    ).one()
    total_users = source_row.total
    email_users = int(source_row.email or 0)
    phone_users = int(source_row.phone or 0)

    # 以下为看板增强字段：纯新增，上方既有字段的口径与语义一律未改动

    # 内容资产 / 待办 / 今日工具使用 / 近7日活跃：合并为「一次往返的 9 个标量子查询」。
    # 各自仍是对单表的独立 COUNT，但只占 1 次 RTT —— 看板已被 900s 缓存兜底，
    # 且这些计数全部命中主键或索引列（auth_requests.status / feedbacks.status 均有索引）。
    overview_row = (
        await db.execute(
            select(
                select(func.count(Resource.id)).scalar_subquery().label("resources"),
                select(func.count(Plugin.id)).scalar_subquery().label("plugins"),
                select(func.count(Node.id)).scalar_subquery().label("nodes"),
                select(func.count(Tool.id)).scalar_subquery().label("tools"),
                select(func.count(Tutorial.id))
                .where(Tutorial.is_published == True)  # noqa: E712
                .scalar_subquery()
                .label("tutorials_published"),
                select(func.count(AuthRequest.id))
                .where(AuthRequest.status == "pending")
                .scalar_subquery()
                .label("pending_auth"),
                select(func.count(Feedback.id))
                .where(Feedback.status == "pending")
                .scalar_subquery()
                .label("pending_feedback"),
                select(func.count(ToolUsageLog.id))
                .where(ToolUsageLog.created_at >= today_start)
                .scalar_subquery()
                .label("today_tool_usage"),
                select(func.count(func.distinct(Download.user_id)))
                .where(Download.created_at >= trend_start)
                .scalar_subquery()
                .label("active_users_7d"),
            )
        )
    ).one()

    # 注册趋势 (近7天) - 与下载趋势同构：单次 GROUP BY。
    # 口径对齐上方「今日新增注册」：均为 User.created_at 计数，**不排除软删除用户**
    # （全库既有用户统计口径如此，见上方 total_users 注释），避免同一看板内自相矛盾。
    reg_rows = (
        await db.execute(
            select(
                func.date(User.created_at).label("day"),
                func.count(User.id).label("count"),
            )
            .where(User.created_at >= trend_start)
            .group_by(func.date(User.created_at))
        )
    ).all()
    reg_map = {str(row.day): row.count for row in reg_rows}
    registration_trend = [
        {
            "date": (today - timedelta(days=i)).isoformat(),
            "count": reg_map.get((today - timedelta(days=i)).isoformat(), 0),
        }
        for i in range(6, -1, -1)
    ]

    # 资源下载榜 Top5（按累计下载次数，同值按 id 倒序保证结果稳定）
    top_rows = (
        await db.execute(
            select(
                Resource.id,
                Resource.name,
                Resource.download_count,
                Resource.view_count,
                Resource.resource_type,
            )
            .order_by(Resource.download_count.desc(), Resource.id.desc())
            .limit(5)
        )
    ).all()
    top_resources = [
        {
            "id": row.id,
            "name": row.name,
            "download_count": int(row.download_count or 0),
            "view_count": int(row.view_count or 0),
            "resource_type": row.resource_type,
        }
        for row in top_rows
    ]

    # 兼容分支用量（第五轮全量审查 P2-55 之六）：摘除判据必须能在后台直接读到。
    # Redis 降级时 snapshot 返回**空 dict**，刻意不返回 `{event: 0}` ——
    # 一次抖动就显示成「旧客户端已经不再发这些参数」，会诱导把兼容分支摘错。
    legacy_usage = await legacy_usage_snapshot(days=7)

    stats = {
        "today_registrations": today_registrations,
        "today_downloads": today_downloads,
        "conversion_rate": conversion_rate,
        "approved_count": approved_count,
        "total_users": total_users,
        # 复用上方 overview 子查询已算出的同口径计数（`is_published == True`），
        # 避免再发一次全表 count 往返（代码审查 2026-10-10 P2）
        "total_tutorials": int(overview_row.tutorials_published or 0),
        "download_trend": trend,
        "registration_source": {
            "phone": phone_users,
            "email": email_users,
        },
        # —— 看板增强字段（新增，供管理后台数据看板展示）——
        "registration_trend": registration_trend,
        "active_users_7d": int(overview_row.active_users_7d or 0),
        "today_tool_usage": int(overview_row.today_tool_usage or 0),
        "content_overview": {
            "tutorials": int(overview_row.tutorials_published or 0),
            "resources": int(overview_row.resources or 0),
            "plugins": int(overview_row.plugins or 0),
            "nodes": int(overview_row.nodes or 0),
            "tools": int(overview_row.tools or 0),
        },
        "pending": {
            "auth_requests": int(overview_row.pending_auth or 0),
            "feedbacks": int(overview_row.pending_feedback or 0),
        },
        "top_resources": top_resources,
        "legacy_usage": legacy_usage,
    }

    # 缓存 15 分钟 (减少缓存击穿时的全表 count/LIKE 串行扫描)
    try:
        await redis_client.set(cache_key, json.dumps(stats, ensure_ascii=False), ex=900)
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("stats_service.get_dashboard_stats", exc)
    return stats


async def get_public_stats(db: AsyncSession) -> dict:
    """首页公开统计数据 (教程/资源/扩展/用户总数)，不含缓存，由调用方管理缓存策略

    四项 count 合并成**一次**查询（第三轮增量审查 P1-43⑤）：原先是 4 条串行
    COUNT，在缓存击穿（固定 TTL 到期瞬间）会把 4 个 DB 往返串起来。同文件的
    `get_dashboard_stats` 早已用「scalar_subquery 一次取多列」的写法，这里与它对齐。
    """
    row = (
        await db.execute(
            select(
                select(func.count(Tutorial.id))
                .where(Tutorial.is_published == True)  # noqa: E712
                .scalar_subquery()
                .label("total_tutorials"),
                # ⚠️ 资源 / 扩展也要过滤发布态（第二轮复审 C-03）：
                # 此前只有教程加了过滤，于是首页「资源 N 个」把草稿也算进去，
                # 与列表 total 对不上；运营下架内容后数字还不降。
                select(func.count(Resource.id))
                .where(Resource.is_published == True)  # noqa: E712
                .scalar_subquery()
                .label("total_resources"),
                select(func.count(Plugin.id))
                .where(Plugin.is_published == True)  # noqa: E712
                .scalar_subquery()
                .label("total_plugins"),
                # 过滤已注销（软删除）用户：与 get_dashboard_stats 同一口径（P2-34）
                select(func.count(User.id))
                .where(User.deleted_at.is_(None))
                .scalar_subquery()
                .label("total_users"),
            )
        )
    ).one()

    return {
        "total_tutorials": row.total_tutorials,
        "total_resources": row.total_resources,
        "total_plugins": row.total_plugins,
        "total_users": row.total_users,
    }
