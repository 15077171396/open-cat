"""管理后台 - 用户反馈审阅服务"""

import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.feedback import FEEDBACK_STATUSES, Feedback
from app.models.user import User
from app.services.admin_audit_service import audit_changes, record_admin_action
from app.services.feedback_service import to_dict
from app.utils.sanitize import escape_like, sanitize_content


async def admin_list_feedbacks(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    keyword: str | None = None,
    start_at: dt.datetime | None = None,
    end_at: dt.datetime | None = None,
) -> dict:
    """反馈列表（按提交时间倒序，join 用户取提交人账号/昵称）

    `start_at` / `end_at` 按**提交时间**闭区间，且必须是 **naive UTC**
    （路由层用 `utils.time.parse_iso_to_naive_utc` 从前端 ISO 串换算，别在这里猜时区）。
    审核场景「昨天/本周收到了哪些反馈」是最常见的入口，此前只能翻页找。

    状态取值在触库前校验：非法值直接 400，而不是静默返回空列表 ——
    「筛了待处理却是空的」会让人误判成「队列已清空」。
    """
    conditions = []
    if status:
        if status not in FEEDBACK_STATUSES:
            raise AppException(code=400, message="无效的反馈状态")
        conditions.append(Feedback.status == status)
    if start_at is not None:
        conditions.append(Feedback.created_at >= start_at)
    if end_at is not None:
        conditions.append(Feedback.created_at <= end_at)
    if keyword:
        conditions.append(Feedback.content.contains(escape_like(keyword), escape="\\"))

    total = (
        await db.execute(select(func.count(Feedback.id)).where(*conditions))
    ).scalar()
    rows = (
        await db.execute(
            select(Feedback, User.account, User.nickname)
            .outerjoin(User, Feedback.user_id == User.id)
            .where(*conditions)
            .order_by(Feedback.created_at.desc(), Feedback.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [
            to_dict(f, {"account": account, "nickname": nickname})
            for f, account, nickname in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def admin_update_feedback(
    db: AsyncSession, admin: User, feedback_id: int, updates: dict
) -> None:
    """审阅更新：状态流转 / 管理员备注（写入管理操作审计 —— 处置用户反馈需可追溯）"""
    f = (
        await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    ).scalar_one_or_none()
    if not f:
        raise AppException(code=404, message="反馈不存在")

    if updates.get("status") is not None:
        status = updates["status"]
        if status not in FEEDBACK_STATUSES:
            raise AppException(code=400, message="无效的反馈状态")
        f.status = status
    if updates.get("admin_note") is not None:
        f.admin_note = sanitize_content(updates["admin_note"]) or None
    record_admin_action(
        db, admin, "feedback_update",
        target_type="feedback", target_id=feedback_id,
        summary=f"处理反馈 #{feedback_id}（status={f.status}）",
        detail={"fields": sorted(updates.keys()), "changes": audit_changes(f, updates)},
    )
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()


async def admin_delete_feedback(db: AsyncSession, admin: User, feedback_id: int) -> None:
    """删除反馈（硬删除，写入管理操作审计）"""
    f = (
        await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    ).scalar_one_or_none()
    if not f:
        raise AppException(code=404, message="反馈不存在")
    await db.delete(f)
    record_admin_action(
        db, admin, "feedback_delete",
        target_type="feedback", target_id=feedback_id,
        summary=f"删除反馈 #{feedback_id}",
    )
    await db.commit()
