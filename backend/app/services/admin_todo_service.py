"""顶栏待办聚合：把散落在各业务域、需要管理员动手的计数收口成一份清单。

只回答「现在有多少件事等我做」，不复制业务数据：每项只带跳转路径，
点进去仍是原页面处理，避免在这里出现第二套审核 / 回复入口。

权限过滤在服务端完成：管理员看到的每一项，都是他有权限打开的页面。
没有权限的类别直接消失，而不是显示一个点进去 403 的数字。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_request import AuthRequest
from app.models.feedback import Feedback
from app.models.moderation import ContentModerationLog, ContentReport
from app.models.user import User
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

    if can("auth_request:view"):
        count = await _count_pending(db, AuthRequest)
        if count:
            groups.append(_group("auth_request", "实名认证待审", count, "/auth-requests"))

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
