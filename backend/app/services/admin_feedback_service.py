"""管理后台 - 用户反馈审阅服务"""

import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.feedback import FEEDBACK_STATUSES, Feedback
from app.models.user import User
from app.services.admin_audit_service import audit_changes, record_admin_action
from app.services.feedback_service import (
    add_reply,
    list_replies,
    reply_counts_for,
    to_dict,
)
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
    items = [
        to_dict(f, {"account": account, "nickname": nickname})
        for f, account, nickname in rows
    ]
    # 回复数一次聚合：列表页能直接看出哪些反馈已经跟进过（不做逐行 N+1）
    if items:
        counts = await reply_counts_for(db, [item["id"] for item in items])
        for item in items:
            item["reply_count"] = counts.get(item["id"], 0)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def admin_get_feedback(db: AsyncSession, feedback_id: int) -> dict:
    """反馈详情：本体 + 完整回复线程（时间升序），供管理端对话式处理。"""
    f = (
        await db.scalars(select(Feedback).where(Feedback.id == feedback_id))
    ).first()
    if not f:
        raise AppException(code=404, message="反馈不存在")
    data = to_dict(f)
    data["replies"] = await list_replies(db, feedback_id)
    return data


async def admin_add_reply(
    db: AsyncSession, admin: User, feedback_id: int, content: str
) -> dict:
    """管理员回复（用户可见 + 站内通知 + 同事务审计）。

    审计与回复必须同一事务：先按 commit=False 走统一入口（校验、状态流转、
    通知、回复都在事务里），再补审计记录，最后由这里显式提交。
    """
    reply = await add_reply(
        db,
        feedback_id=feedback_id,
        sender="admin",
        sender_id=admin.id,
        content=content,
        notify_user=True,
        commit=False,
    )
    record_admin_action(
        db, admin, "feedback_reply",
        target_type="feedback", target_id=feedback_id,
        summary=f"回复反馈 #{feedback_id}",
        detail={"content": reply["content"]},
    )
    await db.commit()
    return reply


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
