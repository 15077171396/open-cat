"""用户举报服务：机器漏判的人工兜底通道（「有举报入口 + 有人处理」本身是合规要求）。
防滥用：同一用户对同一内容仅可举报一次（唯一约束）；单用户每日举报上限。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, NotFoundError
from app.models.moderation import (
    CONTENT_TYPE_LABELS,
    CONTENT_TYPES,
    ContentReport,
    MAX_REPORTS_PER_USER_PER_DAY,
    REPORT_DETAIL_MAX_LENGTH,
    REPORT_REASONS,
    REPORT_REASON_LABELS,
    REPORT_STATUSES,
    REPORT_STATUS_LABELS,
)
from app.utils.sanitize import sanitize_plain_text


def _report_to_dict(report: ContentReport) -> dict:
    return {
        "id": report.id,
        "reporter_id": report.reporter_id,
        "content_type": report.content_type,
        "content_type_label": CONTENT_TYPE_LABELS.get(report.content_type, report.content_type),
        "target_id": report.target_id,
        "reason": report.reason,
        "reason_label": REPORT_REASON_LABELS.get(report.reason, report.reason),
        "detail": report.detail,
        "status": report.status,
        "status_label": REPORT_STATUS_LABELS.get(report.status, report.status),
        "handler_id": report.handler_id,
        "handled_at": report.handled_at.isoformat() if report.handled_at else None,
        "handle_note": report.handle_note,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


async def submit_report(
    db: AsyncSession,
    reporter_id: int,
    *,
    content_type: str,
    target_id: str,
    reason: str,
    detail: str | None = None,
) -> dict:
    """提交举报（同一用户对同一内容仅一次）"""
    if content_type not in CONTENT_TYPES:
        raise AppException(code=400, message="不支持的举报类型")
    target_id = (target_id or "").strip()
    if not target_id:
        raise AppException(code=400, message="缺少举报对象")
    if len(target_id) > 64:
        raise AppException(code=400, message="举报对象标识无效")
    if reason not in REPORT_REASONS:
        raise AppException(code=400, message="举报理由无效")

    detail = (detail or "").strip()
    if len(detail) > REPORT_DETAIL_MAX_LENGTH:
        raise AppException(
            code=400, message=f"补充说明不能超过 {REPORT_DETAIL_MAX_LENGTH} 字"
        )

    # 每日上限：防止批量恶意举报灌满审核队列
    # ⚠️ naive UTC（非本地时间）：与 created_at 列（UTC）比较，且 aware 与 naive
    # 混用会抛 TypeError（第二轮增量审查 2026-10-10 P1）
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    today_count = (
        await db.execute(
            select(func.count(ContentReport.id)).where(
                ContentReport.reporter_id == reporter_id,
                ContentReport.created_at >= since,
            )
        )
    ).scalar() or 0
    if today_count >= MAX_REPORTS_PER_USER_PER_DAY:
        raise AppException(code=429, message="今日举报次数已达上限，请明日再试")

    report = ContentReport(
        reporter_id=reporter_id,
        content_type=content_type,
        target_id=target_id,
        reason=reason,
        # 举报详情同样可能含违规内容，剥标签后再入库
        detail=sanitize_plain_text(detail) or None,
        status="pending",
    )
    try:
        async with db.begin_nested():
            db.add(report)
            await db.flush()
    except IntegrityError:
        raise AppException(code=400, message="您已举报过该内容，我们会尽快处理")
    await db.commit()
    # ⚠️ 必须 refresh 再序列化：`created_at` 是 server_default=func.now()，INSERT 之后
    # 该属性处于**未加载**状态（`expire_on_commit=False` 只保住已加载的字段）。
    # 直接在异步上下文里读它 → 触发一次同步 SELECT → MissingGreenlet → 500，
    # 而事务已提交（数据在库里），用户重试只会得到「已举报过」，功能等于不可用。
    await db.refresh(report)
    return _report_to_dict(report)


async def list_reports(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    content_type: str | None = None,
) -> dict:
    """管理端举报列表（待处理优先，时间倒序）"""
    conditions = []
    if status:
        if status not in REPORT_STATUSES:
            raise AppException(code=400, message="无效的举报状态")
        conditions.append(ContentReport.status == status)
    if content_type:
        conditions.append(ContentReport.content_type == content_type)

    total = (
        await db.execute(select(func.count(ContentReport.id)).where(*conditions))
    ).scalar()
    rows = (
        await db.execute(
            select(ContentReport)
            .where(*conditions)
            # 待处理的排前面：审核队列的语义就是「先解决没处理的」
            .order_by(
                (ContentReport.status == "pending").desc(),
                ContentReport.created_at.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [_report_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def handle_report(
    db: AsyncSession, report_id: int, handler_id: int, status: str, note: str | None = None
) -> dict:
    """处理举报：resolved=已处理 / dismissed=驳回（判定为误报）"""
    if status not in ("resolved", "dismissed"):
        raise AppException(code=400, message="无效的举报处理结果")
    report = (
        await db.execute(select(ContentReport).where(ContentReport.id == report_id))
    ).scalar_one_or_none()
    if not report:
        raise NotFoundError("举报记录不存在")
    if report.status != "pending":
        raise AppException(code=400, message="该举报已处理，无需重复操作")
    report.status = status
    report.handler_id = handler_id
    report.handled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    report.handle_note = (note or None) or None
    await db.commit()
    return _report_to_dict(report)


async def count_pending(db: AsyncSession) -> int:
    """待处理举报数（管理端角标）"""
    return (
        await db.execute(
            select(func.count(ContentReport.id)).where(ContentReport.status == "pending")
        )
    ).scalar() or 0


async def list_my_reports(
    db: AsyncSession, reporter_id: int, page: int = 1, page_size: int = 50
) -> dict:
    """我的举报列表（用户视角：能看到处理状态与结果）"""
    conditions = [ContentReport.reporter_id == reporter_id]
    total = (
        await db.execute(select(func.count(ContentReport.id)).where(*conditions))
    ).scalar()
    rows = (
        await db.execute(
            select(ContentReport)
            .where(*conditions)
            .order_by(ContentReport.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [_report_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


