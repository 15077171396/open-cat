"""定时任务 - VIP 过期降级、百度收录推送等

调度方式（2026-10-27 起）：
    任务**不是**写死 `add_job` 的，而是「代码里注册 handler（`JOB_FUNCS`）+ 库里的配置表
    （`scheduled_job_configs`：跑不跑 / 什么周期）」两者合并后注册。对齐循环每 5 秒
    比一次配置指纹，变了就热重载（add / remove job），并把「我按哪份配置在跑」写回
    Redis `ops:jobs:applied`（同时是存活心跳）。

    ⚠️ **配置没变时绝不重复 add_job**：`replace_existing=True` 会重排 next_run_time，
    每 5 秒重排一次等于让「每 1 分钟」的任务永远等不到下一次执行。

两条跨进程通道（键名定义在 `app/services/scheduled_job_service.py`）：
    - 配置表 → 本文件的 `_sync_once`：改周期/启停的生效路径；
    - `ops:jobs:commands` 队列 → `_consume_commands`：后台「立即执行一次」。
"""

import asyncio
import functools
import json
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from sqlalchemy import select, update

from app.config import settings
from app.core.monitoring import init_monitoring
from app.database import async_session
from app.models.scheduled_job_config import ScheduledJobConfig
from app.models.tutorial import Tutorial
from app.models.tutorial_node import TutorialNode
from app.models.user import User
from app.redis import log_redis_degraded, redis_client
from app.services import scheduled_job_service as sjs
from app.services.admin_audit_service import (
    AUDIT_LOG_RETENTION_DAYS,
    cleanup_expired_audit_logs,
)
from app.services.baidu_push_service import push_urls_to_baidu
from app.services import recycle_bin_service
from app.services.content_cache import invalidate_tutorials
from app.services.data_quality_service import collect_issues
from app.services.search_log_service import SEARCH_LOG_RETENTION_DAYS, cleanup_old as cleanup_search_logs_db
from app.services.content_view_service import (
    CONTENT_VIEW_RETENTION_DAYS,
    cleanup_old as cleanup_content_views_db,
)
from app.services.indexnow_service import push_urls_to_indexnow
from app.services.system_status_service import record_data_quality, record_job_run
from app.services.view_counter import flush_pending_views

# 配置对齐周期（秒）：改完周期最多这么久生效。5 秒是「感觉得到」与「别太吵」的折中
# （每轮只是一条 8 行的 SELECT）。
CONFIG_POLL_SECONDS = 5
# 命令队列轮询周期（秒）：决定「点立即执行」到真正开始跑的延迟
COMMAND_POLL_SECONDS = 1
# 消费命令出错后的退避（避免 Redis 抖动时打满日志）
COMMAND_ERROR_BACKOFF_SECONDS = 5


def _judge_result(result) -> tuple[bool, str]:
    """任务返回值 → (是否成功, 摘要)。

    ⚠️ 不用「抛异常」判定失败：这些任务刻意吞掉异常以免影响下一次调度，
    若包装层靠捕获异常判定，失败会全部显示成成功（比没有心跳更糟）。
    """
    if result is False:
        return False, "执行失败（详见应用日志）"
    if isinstance(result, str):
        return True, result
    return True, ""


async def _run_guarded(job_id: str, fn=None, *, manual: bool = False):
    """执行一个任务并写心跳；同一任务不并发。

    `_running` 是**进程内闩锁**，同时挡住「调度触发」与「手动触发」：
    定时任务里有清理、降级这类写操作，两条同时跑会互相踩（比如同一批审计日志被删两次、
    或 flush_view_counts 竞争同一批 Redis 增量）。APScheduler 的 max_instances 只管
    它自己调度的作业，手动触发是直接调函数，绕过那一层 —— 所以闩锁必须放在这里。
    """
    handler = fn or JOB_FUNCS.get(job_id)
    if handler is None:
        logger.error(f"未知的定时任务 id: {job_id}（未在 JOB_FUNCS 注册），已忽略")
        return None
    if job_id in _running:
        logger.warning(f"任务 {job_id} 正在执行，忽略本次触发（manual={manual}）")
        return None

    _running.add(job_id)
    ok, detail = True, ""
    try:
        result = await handler()
    except Exception as exc:  # noqa: BLE001 记录失败后原样上抛（APScheduler 自己会兜住）
        ok, detail = False, f"{type(exc).__name__}: {exc}"
        raise
    else:
        ok, detail = _judge_result(result)
        return result
    finally:
        _running.discard(job_id)
        # record_job_run 自身绝不抛异常，不会盖掉任务的真实结果
        await record_job_run(job_id, ok, f"[手动] {detail}" if manual and detail else detail)


def _with_heartbeat(job_id: str, fn):
    """包一层心跳：任务结束后把「跑了没 / 成功没 / 做了什么」写进 Redis。

    为什么要有它：定时任务是**最容易静默失效**的一环 —— 任务没跑、跑了失败、
    或者压根没注册，应用日志里都只是少了一行，没人会发现。后台「系统状态」卡与
    「定时任务」页读这份心跳。

    约定：被包装的任务 **成功返回 str（作为 detail）/ None**，失败返回 `False`。
    """

    @functools.wraps(fn)
    async def _runner(*args, **kwargs):
        # 现有 handler 都是零参；带参调用交给 `_run_guarded` 之外的路径（当前没有）
        return await _run_guarded(job_id, lambda: fn(*args, **kwargs))

    return _runner


async def check_vip_expiry():
    """VIP 过期降级任务 - 每日凌晨 02:00 (北京时间) 执行"""
    logger.info("开始执行 VIP 过期检查...")
    now = datetime.now(timezone.utc)

    try:
        async with async_session() as db:
            result = await db.execute(
                update(User)
                .where(
                    User.role == "vip",
                    User.vip_expire_time < now,
                )
                .values(role="user", vip_expire_time=None)
            )
            updated = result.rowcount

            await db.commit()
            if updated:
                logger.info(f"VIP 过期降级完成，共处理 {updated} 个用户")
                return f"降级 {updated} 个用户"
            logger.info("无过期 VIP 用户")
            return "无过期用户"
    except Exception:
        # 任务异常仅记日志，不影响下一次调度（与 push_new_urls_to_baidu 的保护策略一致）
        logger.exception("VIP 过期检查任务执行失败")
        return False


async def cleanup_audit_logs():
    """审计日志保留期清理任务 - 每日凌晨 04:00（北京时间）执行。
    审计表只增不减，保留 365 天；删除逻辑在 admin_audit_service（可单测），本任务只负责开会话+提交。"""
    logger.info("开始清理过期审计日志...")
    try:
        async with async_session() as db:
            deleted = await cleanup_expired_audit_logs(db)
            await db.commit()
        if deleted:
            logger.info(f"审计日志清理完成，共删除 {deleted} 条（保留 {AUDIT_LOG_RETENTION_DAYS} 天）")
            return f"清理 {deleted} 条"
        logger.info("审计日志无过期记录")
        return "无过期记录"
    except Exception:
        # 任务异常仅记日志，不影响下一次调度（与其它任务同一保护策略）
        logger.exception("审计日志清理任务执行失败")
        return False


# 每个板块单次推送的 URL 上限：无 LIMIT 时一次批量导入/分类改名会把全部存量页当「最近更新」
# 推出去，烧光百度当日配额，还会让调度器次日抛 MaxInstancesReachedError；
# 各板块取最近更新的 N 条，被截断的存量页靠 sitemap 兜底发现（推送只是加速通道）
_MAX_PUSH_URLS_PER_TYPE = 500


async def collect_recent_urls(hours: int = 26) -> list[str]:
    """收集最近 N 小时内新增/更新的内容页 URL（百度/IndexNow 推送共用）。

    比自然日多 2 小时冗余，规避任务执行时刻漂移导致的边界漏推。
    """
    base = settings.SITE_BASE_URL.rstrip("/")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    urls: list[str] = []
    async with async_session() as db:
        tutorials = (await db.execute(
            select(Tutorial.id)
            .where(Tutorial.is_published == True, Tutorial.updated_at >= since)  # noqa: E712
            .order_by(Tutorial.updated_at.desc(), Tutorial.id.desc())
            .limit(_MAX_PUSH_URLS_PER_TYPE)
        )).scalars().all()
        urls += [f"{base}/tutorials/{t}" for t in tutorials]

        chapters = (await db.execute(
            select(TutorialNode.tutorial_id, TutorialNode.id)
            .join(Tutorial, Tutorial.id == TutorialNode.tutorial_id)
            .where(Tutorial.is_published == True, TutorialNode.updated_at >= since)  # noqa: E712
            .order_by(TutorialNode.updated_at.desc(), TutorialNode.id.desc())
            .limit(_MAX_PUSH_URLS_PER_TYPE)
        )).all()
        urls += [f"{base}/tutorials/{t}/c/{n}" for t, n in chapters]

    # 去重：同一条 URL 重复提交既浪费当日共享配额，也会让推送通道被判低质
    return list(dict.fromkeys(urls))


async def push_new_urls_to_baidu():
    """百度收录推送任务 - 每日凌晨 03:00

    推送最近 26 小时内新增/更新的内容页 URL。存量页面的发现依赖 sitemap
    提交，本任务只负责加速新内容被发现。未配置 token 时自动跳过。
    """
    if not settings.BAIDU_PUSH_TOKEN:
        return "未配置 BAIDU_PUSH_TOKEN，跳过"

    logger.info("开始执行百度收录推送...")
    try:
        urls = await collect_recent_urls()
        if not urls:
            logger.info("近 26 小时无新增/更新内容，跳过百度推送")
            return "近 26 小时无更新"

        result = await push_urls_to_baidu(urls)
        logger.info(f"百度推送任务结束: 待推 {len(urls)} 条, 成功 {result['success']} 条")
        return f"推送 {result['success']}/{len(urls)} 条"
    except Exception as e:  # noqa: BLE001 推送失败不影响其它定时任务
        logger.warning(f"百度推送任务异常（已忽略）: {e}")
        return False


async def push_new_urls_to_indexnow():
    """Bing IndexNow 推送任务 - 每日凌晨 03:10（北京时间）

    与百度推送共用 URL 收集逻辑。未配置密钥时自动跳过。
    """
    if not settings.INDEXNOW_KEY:
        return "未配置 INDEXNOW_KEY，跳过"

    logger.info("开始执行 IndexNow 推送...")
    try:
        urls = await collect_recent_urls()
        if not urls:
            logger.info("近 26 小时无新增/更新内容，跳过 IndexNow 推送")
            return "近 26 小时无更新"

        result = await push_urls_to_indexnow(urls)
        logger.info(f"IndexNow 推送任务结束: 待推 {len(urls)} 条, 成功 {result['success']} 条")
        return f"推送 {result['success']}/{len(urls)} 条"
    except Exception as e:  # noqa: BLE001 推送失败不影响其它定时任务
        logger.warning(f"IndexNow 推送任务异常（已忽略）: {e}")
        return False


async def flush_view_counts():
    """浏览量落库 - 每分钟执行。

    详情页不再每次访问都 UPDATE 一行（见 services/view_counter），改为 Redis 累加，
    由本任务合并成一条 UPDATE 写回。间隔取 1 分钟：既能把写放大降到 1/60，
    又保证「数据库里的 view_count」最多滞后一分钟。
    """
    try:
        async with async_session() as db:
            await flush_pending_views(db)
        return ""
    except Exception:
        # 与其它任务一致：只记日志，不影响下一次调度
        logger.exception("浏览量落库任务执行失败")
        return False


async def data_quality_check():
    """数据质量只读巡检 - 每日 05:00（北京时间）执行。

    只查不删（见 services/data_quality_service 的模块说明）：
    负积分、过期未降级的 VIP、指向已删内容的孤儿收藏/下载/学习记录。
    结果写 Redis，由后台「系统状态」卡展示；有问题时打 WARNING 便于 Sentry 带走。
    """
    logger.info("开始执行数据质量巡检...")
    try:
        async with async_session() as db:
            result = await collect_issues(db)
        await record_data_quality(result)
        total = result.get("issue_total") or 0
        if total:
            logger.warning(
                "数据质量巡检发现异常："
                + "；".join(f"{i['text']} {i['count']} 条" for i in result.get("issues") or [])
            )
            return f"发现 {total} 条异常"
        logger.info("数据质量巡检通过，无异常")
        return "无异常"
    except Exception:
        # 巡检本身失败绝不能影响其它任务；心跳会把失败状态显示出来
        logger.exception("数据质量巡检任务执行失败")
        return False


async def cleanup_search_logs():
    """站内搜索日志保留期清理 - 每日 04:30（北京时间）。

    搜索日志是「选题素材」而不是审计记录：留 90 天足够看出用户最近在找什么，
    再久既没有决策价值又白占磁盘（单机 2 核 4G，磁盘是最容易先满的资源）。
    """
    logger.info("开始清理过期搜索日志...")
    try:
        async with async_session() as db:
            deleted = await cleanup_search_logs_db(db, SEARCH_LOG_RETENTION_DAYS)
            await db.commit()
        if deleted:
            return f"清理 {deleted} 条"
        return "无过期记录"
    except Exception:
        logger.exception("搜索日志清理任务执行失败")
        return False


async def cleanup_content_views():
    """内容浏览归因记录清理 - 每日 04:40（北京时间），保留 180 天。

    保留期比搜索词（90 天）长：归因要看「申请认证前看过什么」，
    而「看内容 → 决定付费」的周期通常以周计，太短会把有效归因删掉。
    """
    logger.info("开始清理过期内容浏览记录...")
    try:
        async with async_session() as db:
            deleted = await cleanup_content_views_db(db, CONTENT_VIEW_RETENTION_DAYS)
            await db.commit()
        return f"清理 {deleted} 条" if deleted else "无过期记录"
    except Exception:
        logger.exception("内容浏览记录清理任务执行失败")
        return False


async def purge_recycle_bin():
    """回收站到期清理 - 每日 04:50（北京时间）。

    只清教程与评论：用户账号不自动清理（恢复账号是人工决策）；有购买记录的教程
    跳过，避免删掉用户已经付费的内容。教程先清章节/网盘链接再删主行。
    """
    logger.info("开始清理过期回收站...")
    try:
        async with async_session() as db:
            result = await recycle_bin_service.purge_expired(db)
            await db.commit()
        if result["tutorials"]:
            await invalidate_tutorials()
        parts = []
        if result["tutorials"]:
            parts.append(f"教程 {result['tutorials']} 篇")
        if result["comments"]:
            parts.append(f"评论 {result['comments']} 条")
        skipped = result.get("tutorials_skipped") or 0
        if skipped:
            parts.append(f"跳过有购买记录教程 {skipped} 篇")
        return "清理 " + " / ".join(parts) if parts else "无过期记录"
    except Exception:
        logger.exception("回收站清理任务执行失败")
        return False


# ---------------------------------------------------------------------------
# 任务注册表：id → handler
# ---------------------------------------------------------------------------
# **这是唯一可被调度的任务集合**。后台能改的只有「跑不跑 / 什么周期」，
# 不能凭空造出新任务 —— 新增任务必须在这里注册 + 在 JOB_SPECS 里声明（测试双向比对）。
JOB_FUNCS: dict[str, object] = {
    "check_vip_expiry": check_vip_expiry,
    "push_new_urls_to_baidu": push_new_urls_to_baidu,
    "push_new_urls_to_indexnow": push_new_urls_to_indexnow,
    "cleanup_audit_logs": cleanup_audit_logs,
    "cleanup_search_logs": cleanup_search_logs,
    "cleanup_content_views": cleanup_content_views,
    "purge_recycle_bin": purge_recycle_bin,
    "flush_view_counts": flush_view_counts,
    "data_quality_check": data_quality_check,
}

# 进程内「正在执行」的任务（调度触发与手动触发共用，见 _run_guarded）
_running: set[str] = set()

# 上一次**已对齐**的配置指纹与快照。没变化时只刷新存活心跳，绝不重复 add_job
# （否则 replace_existing 每 5 秒重排一次，「每 1 分钟」的任务永远等不到下一次）
_applied_hash: str | None = None
_applied_jobs: dict[str, dict] = {}


async def _load_configs() -> dict[str, sjs.JobConfig]:
    """读配置表 → 生效配置；任何异常都回退到代码默认值。

    回退而不是抛：配置读不出来时「按代码默认周期继续跑」远好于「整片任务停摆」。
    这时页面上会显示默认周期且标记「待生效」，运维能看出不对劲。
    """
    try:
        async with async_session() as db:
            rows = (await db.execute(select(ScheduledJobConfig))).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取定时任务配置失败，按代码默认值运行: {exc}")
        return dict(sjs.DEFAULT_CONFIGS)

    by_id = {row.job_id: row for row in rows}
    return {
        spec.id: sjs.config_from_row(spec, sjs.config_view(by_id.get(spec.id)))
        for spec in sjs.JOB_SPECS
    }


def _trigger_for(cfg: sjs.JobConfig):
    """配置 → APScheduler 触发器。

    daily 显式指定 Asia/Shanghai：容器系统时区通常是 UTC，不指定会整体晚 8 小时
    （VIP 过期用户会多享受 8 小时付费权益）。
    """
    if cfg.kind == "interval":
        return IntervalTrigger(minutes=cfg.interval_minutes)
    return CronTrigger(hour=cfg.hour, minute=cfg.minute, timezone="Asia/Shanghai")


async def _write_applied(configs: dict[str, sjs.JobConfig], config_hash: str) -> None:
    """把「我按哪份配置在跑」写回 Redis（配置生效状态 + 调度器存活信号，TTL 120s）"""
    payload = json.dumps(
        {
            "hash": config_hash,
            "at": int(time.time()),
            "jobs": {
                spec.id: {
                    "enabled": (configs.get(spec.id) or sjs.default_config(spec)).enabled,
                    "schedule": (configs.get(spec.id) or sjs.default_config(spec)).schedule_text,
                }
                for spec in sjs.JOB_SPECS
            },
        },
        ensure_ascii=False,
    )
    try:
        await redis_client.set(sjs.APPLIED_KEY, payload, ex=sjs.APPLIED_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        # 写不进去只影响后台的「生效状态」显示，任务本身照常跑
        log_redis_degraded("scheduler.write_applied", f"配置生效标记写入失败: {exc}")


async def _apply_configs(scheduler: AsyncIOScheduler, configs: dict[str, sjs.JobConfig]) -> None:
    """把配置对齐到 scheduler：启用的 add_job，停用的 remove_job。"""
    for spec in sjs.JOB_SPECS:
        cfg = configs.get(spec.id) or sjs.default_config(spec)
        if not cfg.enabled:
            if scheduler.get_job(spec.id) is not None:
                scheduler.remove_job(spec.id)
                logger.info(f"定时任务已停用并移除: {spec.id}")
            continue
        scheduler.add_job(
            _with_heartbeat(spec.id, JOB_FUNCS[spec.id]),
            _trigger_for(cfg),
            id=spec.id,
            replace_existing=True,
            # 容器暂停/事件循环阻塞导致错过触发时的补跑窗口：
            # 日报 1 小时（02:00 被卡住也不该跳过整天）；每分钟任务 2 分钟（增量在 Redis 里不会丢）
            misfire_grace_time=120 if cfg.kind == "interval" else 3600,
        )


async def _sync_once(scheduler: AsyncIOScheduler) -> bool:
    """执行一次「配置 → 调度器」对齐；返回是否发生了重载。"""
    global _applied_hash, _applied_jobs

    configs = await _load_configs()
    config_hash = sjs.config_fingerprint(configs)

    if config_hash != _applied_hash:
        await _apply_configs(scheduler, configs)
        _applied_hash = config_hash
        _applied_jobs = {
            spec.id: {
                "enabled": (configs.get(spec.id) or sjs.default_config(spec)).enabled,
                "schedule": (configs.get(spec.id) or sjs.default_config(spec)).schedule_text,
            }
            for spec in sjs.JOB_SPECS
        }
        await _write_applied(configs, config_hash)
        logger.info(f"定时任务配置已对齐（{len(JOB_FUNCS)} 个任务，hash={config_hash}）")
        return True

    # 配置没变：只刷新存活心跳（不重排任务）
    await _write_applied(configs, config_hash)
    return False


async def _watch_configs(scheduler: AsyncIOScheduler) -> None:
    """每 5 秒对齐一次配置（改周期/启停的生效路径）"""
    while True:
        try:
            await _sync_once(scheduler)
        except Exception:  # noqa: BLE001 单轮失败不该让循环退出
            logger.exception("定时任务配置对齐失败（下个周期自动重试）")
        await asyncio.sleep(CONFIG_POLL_SECONDS)


async def _handle_command(raw) -> None:
    """执行一条「立即执行」命令（payload 由 API 侧写入）"""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"忽略非法的定时任务命令: {raw!r}")
        return
    job_id = payload.get("job_id") if isinstance(payload, dict) else None
    if job_id not in JOB_FUNCS:
        logger.warning(f"忽略未知任务的命令: {job_id}")
        return
    by = (payload.get("by") or "")[:120]
    logger.info(f"收到手动执行命令: {job_id}（操作者 {by or '未知'}）")
    await _run_guarded(job_id, manual=True)


async def _consume_commands() -> None:
    """每秒取一条命令执行（跨进程「立即执行一次」的消费端）"""
    while True:
        try:
            raw = await redis_client.lpop(sjs.COMMAND_QUEUE_KEY)
            if raw is not None:
                await _handle_command(raw)
                continue
        except Exception:  # noqa: BLE001
            logger.exception("消费定时任务命令失败，稍后重试")
            await asyncio.sleep(COMMAND_ERROR_BACKOFF_SECONDS)
            continue
        await asyncio.sleep(COMMAND_POLL_SECONDS)


async def main():
    """定时任务主入口"""
    # 定时任务跑在独立容器里，不经过 create_app()，需单独初始化错误监控。
    # 否则「任务静默失败」只能靠人工翻日志文件才能发现（P0-1）。
    init_monitoring()

    scheduler = AsyncIOScheduler()

    # 启动即对齐一次：配置读不到时用代码默认值（等价于升级前的静态注册行为）
    await _sync_once(scheduler)
    scheduler.start()
    logger.info("定时任务调度器已启动")

    watcher = asyncio.create_task(_watch_configs(scheduler))
    consumer = asyncio.create_task(_consume_commands())

    try:
        # 保持运行
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        for task in (watcher, consumer):
            task.cancel()
        scheduler.shutdown()
        logger.info("定时任务调度器已停止")


if __name__ == "__main__":
    asyncio.run(main())
