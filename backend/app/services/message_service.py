"""消息服务"""

from sqlalchemy import select, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.models.user import User
from app.core.exceptions import NotFoundError
from app.utils.sanitize import escape_like


VALID_TYPES = {"system", "comment", "like", "private"}


async def list_messages(
    db: AsyncSession,
    user: User,
    page: int = 1,
    page_size: int = 6,
    type: str = None,
    keyword: str = None,
) -> dict:
    """获取消息列表"""
    conditions = [Message.user_id == user.id]
    if type and type != "all":
        conditions.append(Message.type == type)
    if keyword:
        kw = escape_like(keyword)
        conditions.append(
            or_(
                Message.title.contains(kw, escape="\\"),
                Message.content.contains(kw, escape="\\"),
            )
        )

    # 直接对主表 count, 避免 subquery 包裹 (PERF)
    total = (await db.execute(select(func.count(Message.id)).where(*conditions))).scalar()
    query = (
        select(Message).where(*conditions)
        # 同上：批量下架会一次插入多条同秒消息，没有 id 兜底会跨页重复
        .order_by(Message.created_at.desc(), Message.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(query)).scalars().all()

    items = [
        {
            "id": m.id,
            "type": m.type,
            "title": m.title,
            "content": m.content,
            "time": m.created_at.isoformat() if m.created_at else None,
            "read": m.is_read,
            "avatar": m.avatar,
            "link": m.link,
        }
        for m in rows
    ]

    # 未读总数
    unread = (
        await db.execute(
            select(func.count(Message.id)).where(
                Message.user_id == user.id, Message.is_read == False  # noqa
            )
        )
    ).scalar() or 0

    return {"items": items, "total": total, "unread": unread, "page": page, "page_size": page_size}


async def get_message(db: AsyncSession, user: User, message_id: int) -> dict:
    """获取消息详情"""
    m = (
        await db.execute(
            select(Message).where(Message.id == message_id, Message.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not m:
        raise NotFoundError("消息不存在")
    return {
        "id": m.id,
        "type": m.type,
        "title": m.title,
        "content": m.content,
        "time": m.created_at.isoformat() if m.created_at else None,
        "read": m.is_read,
        "avatar": m.avatar,
        "link": m.link,
    }


async def mark_read(db: AsyncSession, user: User, message_id: int) -> None:
    """标记单条消息已读"""
    m = (
        await db.execute(
            select(Message).where(Message.id == message_id, Message.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not m:
        raise NotFoundError("消息不存在")
    if not m.is_read:
        m.is_read = True
        await db.commit()


async def mark_all_read(db: AsyncSession, user: User) -> int:
    """全部已读"""
    result = await db.execute(
        update(Message)
        .where(Message.user_id == user.id, Message.is_read == False)  # noqa
        .values(is_read=True)
    )
    await db.commit()
    return result.rowcount


async def delete_message(db: AsyncSession, user: User, message_id: int) -> None:
    """删除消息"""
    m = (
        await db.execute(
            select(Message).where(Message.id == message_id, Message.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not m:
        raise NotFoundError("消息不存在")
    await db.delete(m)
    await db.commit()


async def get_unread_count(db: AsyncSession, user: User) -> dict:
    """获取未读数"""
    unread = (
        await db.execute(
            select(func.count(Message.id)).where(
                Message.user_id == user.id, Message.is_read == False  # noqa
            )
        )
    ).scalar() or 0
    return {"unread": unread}


