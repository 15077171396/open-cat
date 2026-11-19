"""管理后台 - 数据看板与运维状态"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Literal, Optional

from app.database import get_db, engine
from app.core.degrade import degrade_guard
from app.core.permissions import require_admin
from app.core.sql_metrics import pool_status, sql_metrics_snapshot
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse
from app.api._admin_shared import router

from app.services.stats_service import get_dashboard_stats
from app.services.ai_usage_service import get_usage_summary as get_ai_usage_summary
from app.services.system_status_service import get_system_status
from app.services import ops_metric_service, readiness_service
from app.services import scheduled_job_admin_service as job_admin
from app.services.search_log_service import get_keyword_report
from app.schemas.admin import ActionAck, StatsResult


class MetricOption(BaseModel):
    """趋势面板的指标选项（value/label 与 METRIC_LABELS 同源）"""

    value: str
    label: str


class MetricTrendData(BaseModel):
    """核心指标趋势（看板「系统指标趋势」面板的数据源）"""

    metric: str = ""
    label: str = ""
    hours: int = 48
    available: bool = True
    points: list[dict] = Field(default_factory=list)
    metric_options: list[MetricOption] = Field(default_factory=list)


# 数据看板

@router.get("/dashboard", response_model=ApiResponse[StatsResult])
async def dashboard(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """数据看板"""
    stats = await get_dashboard_stats(db)
    return success_response(stats)


@router.get("/ai-usage", response_model=ApiResponse[StatsResult])
async def ai_usage(
    days: int = Query(30, ge=1, le=90),
    admin: User = Depends(require_admin),
):
    """AI 全站用量与成本护栏状态。独立于 /dashboard：看板整体缓存 15 分钟，
    而用量是排障/应急时最需要「现在这一刻」的数据，故单独一个接口（Redis 直读无缓存）"""
    return success_response(await get_ai_usage_summary(days))


class MetricSummaryItem(BaseModel):
    """单指标概览：当前读数 + 与上一帧的环比"""

    key: str
    label: str
    good_when_up: bool
    value: int | None = None
    previous: int | None = None
    delta: int | None = None


class MetricSummaryData(BaseModel):
    """全指标概览（最近两帧快照对比）"""

    available: bool = True
    frame_count: int = 0
    taken_at: str | None = None
    items: list[MetricSummaryItem] = Field(default_factory=list)


@router.get("/ops-metrics/summary", response_model=ApiResponse[MetricSummaryData])
async def ops_metrics_summary(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """全指标概览：最近两帧快照对比（当前读数 + 环比），供看板多指标卡网格。"""
    data = await ops_metric_service.latest_summary(db)
    return success_response(data)


@router.get("/ops-metrics", response_model=ApiResponse[MetricTrendData])
async def ops_metrics(
    metric: str = Query("users_total", max_length=64, description="指标键（见 metric_options）"),
    hours: int = Query(48, ge=2, le=720),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """核心指标趋势：每小时快照的回看（注册用户/教程/评论总量、待办积压、登录失败）。

    选项与展示名都从 `METRIC_LABELS` 同源下发，前端不必维护第二份字典；
    快照表还没建（迁移未执行）时 `available=False`，面板显示「尚未采集」。
    """
    data = await ops_metric_service.trend(db, metric, hours)
    data["metric_options"] = [
        {"value": key, "label": label} for key, label in ops_metric_service.METRIC_LABELS.items()
    ]
    return success_response(data)


@router.get("/search-keywords", response_model=ApiResponse[StatsResult])
async def search_keywords(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """站内搜索词报告：热搜榜 / **零结果榜**（= 缺哪些内容）/ 模块分布。

    零结果榜是这张表最有价值的产出：用户搜了但站内没有，直接就是选题清单。
    `available=False` 表示还没采集到数据（或迁移未执行），前端据此显示「尚未采集」。
    """
    return success_response(await get_keyword_report(db, days, limit))


@router.get("/system-status", response_model=ApiResponse[StatsResult])
async def system_status(
    admin: User = Depends(require_admin),
):
    """运维状态：依赖探活 / 库版本 / 日志落盘 / 备份年龄 / 告警通道 / 定时任务心跳 / 数据巡检。

    **只读**，与 /dashboard 分开是刻意的：
    看板缓存 15 分钟且偏业务数据，而这里要的是「此刻服务是否健康」，
    缓存 30 秒即可；且它不依赖任何业务表，数据库故障时依然能回答「哪里坏了」。
    """
    status = await get_system_status()
    # 池水位 / 慢 SQL 计数是瞬时值，不进 get_system_status 的缓存字典，路由层现读
    with degrade_guard("system_status.pool_metrics", detail="连接池/慢 SQL 指标读取失败，标记未知"):
        status["db_pool"] = pool_status(engine)
        status["sql"] = sql_metrics_snapshot()
    return success_response(status)


@router.get("/readiness", response_model=ApiResponse[StatsResult])
async def deployment_readiness(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """部署体检：存储 / 站点地址 / 邮件 / 短信 / AI / 支付六项是否真的可用。

    与 `/system-status` 的分工：那边回答「依赖是否活着」（MySQL/Redis/备份/心跳），
    这边回答「能力是否可用」。共同点是都只读，且复用 `dashboard:view` ——
    新建权限码必须配「补种给已有角色」的迁移，而这里的信息层级与系统状态卡相同。

    判定口径复用既有实现（邮件走 `mail_settings_service.channel_status`，
    AI/短信/支付走 integration 配置），避免「体检说好、实际报错」的分裂。
    """
    return success_response(await readiness_service.collect(db))


@router.get("/scheduled-jobs", response_model=ApiResponse[StatsResult])
async def scheduled_jobs(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """定时任务：目录 + 库配置 + 心跳 + 调度器生效状态。

    读的权限复用 `dashboard:view`（与其他只读运维接口同层级）；**写**走 `scheduler:manage`。
    返回里带 `apply`：`pending` 表示配置已存库但调度器还没对齐（≤5 秒），
    `alive` 表示调度器容器最近 60 秒有没有心跳 —— 这两项决定前端能不能点「立即执行」。
    """
    return success_response(await job_admin.list_jobs(db))


class ScheduledJobUpdate(BaseModel):
    """改配置：**部分更新**，未传的字段保持原值。

    字段范围只管「形状」，组合校验（daily 却传 interval_minutes、interval 漏间隔）
    在 service 里做 —— 那类错误 pydantic 表达不了。
    """

    enabled: Optional[bool] = None
    schedule_kind: Optional[Literal["daily", "interval"]] = None
    hour: Optional[int] = Field(None, ge=0, le=23)
    minute: Optional[int] = Field(None, ge=0, le=59)
    interval_minutes: Optional[int] = Field(None, ge=1, le=10080)


@router.put("/scheduled-jobs/{job_id}", response_model=ApiResponse[ActionAck])
async def update_scheduled_job(
    job_id: str,
    req: ScheduledJobUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """启停 / 改周期。

    ⚠️ 保存成功 ≠ 已生效：调度器每 5 秒对齐一次，页面据 `apply.pending` 提示。
    写库与审计同事务提交；配置没变化时不写库也不写审计（避免重复保存产生噪音）。
    """
    result = await job_admin.update_job(
        db,
        admin,
        job_id,
        enabled=req.enabled,
        schedule_kind=req.schedule_kind,
        hour=req.hour,
        minute=req.minute,
        interval_minutes=req.interval_minutes,
    )
    message = "配置已保存，最多 5 秒内生效" if result.get("changed") else "配置未变化"
    return success_response(result, message)


@router.post("/scheduled-jobs/{job_id}/run", response_model=ApiResponse[ActionAck])
async def run_scheduled_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """立即执行一次（异步：入队后由调度器容器执行）。

    调度器没在跑时返回 503 而不是「成功入队」—— 队列里的命令不会有人消费，
    假装成功会让管理员以为已经跑过了。
    """
    result = await job_admin.trigger_run(db, admin, job_id)
    return success_response(result, "已下发执行命令，请稍后刷新查看结果")
