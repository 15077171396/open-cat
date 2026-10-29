"""公告服务"""

from datetime import datetime, timezone

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.announcement import Announcement
from app.core.exceptions import NotFoundError


def public_conditions() -> tuple:
    """公开可见的公告条件（列表 / 详情 / 首页公告条**必须共用**这一份）。

    定时发布的关键：`publish_at` 未到点就不算可见，即使 `is_published=1`。
    三处各写一遍的话，最容易漏掉首页那条 —— 表现是「详情页打不开但首页已经滚出来了」。
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (
        Announcement.is_published == True,  # noqa: E712
        or_(Announcement.publish_at.is_(None), Announcement.publish_at <= now),
    )


async def list_announcements(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """已发布公告列表"""
    conditions = public_conditions()
    base = select(Announcement).where(*conditions)
    # 直接对主表 count, 避免 subquery 包裹 (PERF)
    total = (
        await db.execute(select(func.count(Announcement.id)).where(*conditions))
    ).scalar()
    query = (
        base.order_by(Announcement.published_at.desc(), Announcement.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(query)).scalars().all()
    items = [
        {
            "id": a.id,
            "title": a.title,
            "content": a.content,
            "tag": a.tag,
            "color": a.color,
            "published_at": a.published_at.isoformat() if a.published_at else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_announcement(db: AsyncSession, announcement_id: int) -> dict:
    """公告详情"""
    a = (
        await db.execute(
            select(Announcement).where(
                Announcement.id == announcement_id,
                *public_conditions(),
            )
        )
    ).scalar_one_or_none()
    if not a:
        raise NotFoundError("公告不存在")
    return {
        "id": a.id,
        "title": a.title,
        "content": a.content,
        "tag": a.tag,
        "color": a.color,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


async def list_latest_announcements(db: AsyncSession, limit: int = 5) -> list:
    """最新已发布公告（轻量字段，首页公告条滚动展示用，不拉 Text 正文）"""
    rows = (
        await db.execute(
            select(Announcement)
            .options(load_only(Announcement.id, Announcement.title, Announcement.tag, Announcement.color, Announcement.published_at))
            .where(*public_conditions())
            .order_by(Announcement.published_at.desc(), Announcement.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": a.id,
            "title": a.title,
            "tag": a.tag,
            "color": a.color,
            "published_at": a.published_at.isoformat() if a.published_at else None,
        }
        for a in rows
    ]
