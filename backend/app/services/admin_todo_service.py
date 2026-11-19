"""顶栏待办聚合：把散落在各业务域、需要管理员动手的计数收口成一份清单。

只回答「现在有多少件事等我做」，不复制业务数据：每项只带跳转路径，
点进去仍是原页面处理，避免在这里出现第二套审核 / 回复入口。

权限过滤在服务端完成：管理员看到的每一项，都是他有权限打开的页面。
没有权限的类别直接消失，而不是显示一个点进去 403 的数字。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback
from app.models.moderation import ContentModerationLog, ContentReport
from app.models.user import User
from app.core.degrade import degrade_guard
from app.config import settings
from app.utils.time import utcnow
from app.services import ai_usage_service, rbac_service, system_status_service


async def _count_pending(db: AsyncSession, model) -> int:
    statement = select(func.count()).select_from(model).where(model.status == "pending")
    return int((await db.execute(statement)).scalar() or 0)


def _group(
    key: str,
    label: str,
    count: int,
    path: str,
    *,
    severity: str = "warning",
    detail: str = "",
) -> dict:
    return {
        "key": key,
        "label": label,
        "count": count,
        "path": path,
        "severity": severity,
        "detail": detail,
    }


async def get_admin_todos(db: AsyncSession, user: User) -> dict:
    """聚合当前管理员的待办类别（只含有权限且计数大于 0 的项）。"""
    rbac = await rbac_service.get_user_rbac(db, user.id)
    is_super = await rbac_service.is_super_admin(rbac)
    permissions = set(rbac.get("perms") or [])

    def can(code: str) -> bool:
        return is_super or code in permissions

    groups: list[dict] = []

    if can("report:view"):
        count = await _count_pending(db, ContentReport)
        if count:
            groups.append(
                _group("report", "用户举报待处理", count, "/moderation?tab=reports")
            )

    if can("moderation:view"):
        count = await _count_pending(db, ContentModerationLog)
        if count:
            groups.append(
                _group("moderation", "机审留痕待复核", count, "/moderation?tab=logs")
            )

    if can("feedback:view"):
        count = await _count_pending(db, Feedback)
        if count:
            groups.append(_group("feedback", "用户反馈待处理", count, "/feedback"))

    if can("dashboard:view"):
        jobs = await system_status_service.get_jobs_status()
        failed = [
            job
            for job in jobs
            if job.get("enabled", True) and job.get("ok") is False
        ]
        if failed:
            names = "、".join(str(job.get("label") or job.get("id")) for job in failed[:3])
            more = f" 等 {len(failed)} 个任务" if len(failed) > 3 else ""
            groups.append(
                _group(
                    "scheduled_job",
                    "定时任务执行失败",
                    len(failed),
                    "/scheduled-jobs",
                    severity="error",
                    detail=f"{names}{more}上次执行失败",
                )
            )

        # 登录失败异常（2026-11-19）：近 1 小时失败数超过阈值（LOGIN_FAIL_ALERT_THRESHOLD，
        # 0 = 禁用）时顶栏提醒，跳转登录日志排查。实时计数（不依赖每小时快照），
        # 读取失败按「无异常」处理 —— 待办是提醒，不能因为计数抖动误报或报错。
        threshold = settings.LOGIN_FAIL_ALERT_THRESHOLD
        if threshold > 0:
            from datetime import timedelta

            from app.services import login_log_service

            failed = 0
            with degrade_guard("todo.login_failures", detail="登录失败计数读取失败（按无异常处理）"):
                failed = await login_log_service.count_failed_since(
                    db, utcnow() - timedelta(hours=1)
                )
            if failed > threshold:
                groups.append(
                    _group(
                        "login_failures",
                        "登录失败异常增多",
                        failed,
                        "/login-logs",
                        severity="error",
                        detail=f"近 1 小时登录失败 {failed} 次，超过告警阈值 {threshold}（疑似撞库或口令泄露）",
                    )
                )

        pressure = await ai_usage_service.get_quota_pressure()
        if pressure:
            groups.append(
                _group(
                    "ai_quota",
                    "AI 额度将尽",
                    1,
                    "/",
                    detail=(
                        f"{pressure['label']}已用 {pressure['used']}/{pressure['limit']}，"
                        f"剩余 {pressure['remaining']}"
                    ),
                )
            )

    return {
        "total": sum(group["count"] for group in groups),
        "groups": groups,
    }
