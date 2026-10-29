"""管理后台 · 站内消息通知服务。

**只负责「人工群发系统通知」**（全站 / 指定用户）。

评论回复、内容下架这类**自动**通知不经过这里：它们的触发点在业务侧、事务边界也在业务侧
（评论要和评论同事务、下架要和下架同事务），硬塞进本模块只会把它变成上帝对象。

批次号的意义：一次「发全站」会给每个用户插一行，`batch_id` 让后台能按批次看到
「发了多少人、读了多少」，而不是对着一万行逐条数。
"""

import uuid

from sqlalchemy import case, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.database import generate_id
from app.models.message import Message
from app.models.user import User
from app.utils.sanitize import sanitize_plain_text

MAX_TITLE_LENGTH = 60
MAX_CONTENT_LENGTH = 1000
# 单次「指定用户」的上限：再多就该用「发全站」，否则一条条校验 id 既慢又容易误发
MAX_TARGET_USERS = 500
# 批量 INSERT 的分片大小：全站用户过万时一次性构造上万个 dict，内存与 SQL 长度都不划算
_BATCH_CHUNK = 500


def _validate_link(link: str | None) -> str | None:
    """站内跳转路径：只允许以 `/` 开头的相对路径。

    挡 `javascript:` / `data:` / `//evil.com`（协议相对）—— 这个字段会原样渲染成
    `<Link to={link}>`，写错一次就是站内存储型跳转漏洞。
    """
    value = (link or "").strip()
    if not value:
        return None
    if not value.startswith("/") or value.startswith("//"):
        raise AppException(code=400, message="跳转链接必须是以 / 开头的站内路径")
    return value[:500]


async def send_system_message(
    db: AsyncSession,
    admin_id: int,
    *,
    title: str,
    content: str,
    link: str | None = None,
    target: str = "all",
    user_ids: list[int] | None = None,
) -> dict:
    """群发系统通知，返回 {batch_id, sent_count}"""
    clean_title = sanitize_plain_text((title or "").strip())
    if not clean_title:
        raise AppException(code=400, message="通知标题不能为空")
    if len(clean_title) > MAX_TITLE_LENGTH:
        raise AppException(code=400, message=f"通知标题不能超过 {MAX_TITLE_LENGTH} 字")

    clean_content = sanitize_plain_text((content or "").strip())
    if not clean_content:
        raise AppException(code=400, message="通知内容不能为空")
    if len(clean_content) > MAX_CONTENT_LENGTH:
        raise AppException(code=400, message=f"通知内容不能超过 {MAX_CONTENT_LENGTH} 字")

    safe_link = _validate_link(link)

    if target == "all":
        receivers = (
            await db.execute(select(User.id).where(User.deleted_at.is_(None)))
        ).scalars().all()
    elif target == "users":
        wanted = list(dict.fromkeys(int(uid) for uid in (user_ids or [])))
        if not wanted:
            raise AppException(code=400, message="请选择至少一个接收用户")
        if len(wanted) > MAX_TARGET_USERS:
            raise AppException(
                code=400, message=f"单次最多指定 {MAX_TARGET_USERS} 个用户，更多请使用「全站」"
            )
        receivers = (
            await db.execute(
                select(User.id).where(User.id.in_(wanted), User.deleted_at.is_(None))
            )
        ).scalars().all()
        # 显式报错而不是静默跳过：管理员选了 5 个人却只发出 3 条，必须让他知道
        if len(receivers) != len(wanted):
            missing = sorted(set(wanted) - set(receivers))
            raise AppException(code=400, message=f"以下用户不存在或已注销：{missing}")
    else:
        raise AppException(code=400, message="接收范围参数无效")

    if not receivers:
        raise AppException(code=400, message="没有可接收通知的用户")

    batch_id = uuid.uuid4().hex
    rows = [
        {
            "id": generate_id(),
            "user_id": uid,
            # 系统通知没有具体发送者（管理端发的，不暴露是哪个管理员）
            "sender_id": None,
            "batch_id": batch_id,
            "type": "system",
            "title": clean_title,
            "content": clean_content,
            "avatar": None,
            "link": safe_link,
            "is_read": False,
        }
        for uid in receivers
    ]
    for start in range(0, len(rows), _BATCH_CHUNK):
        await db.execute(insert(Message), rows[start : start + _BATCH_CHUNK])
    # 显式提交：响应返回前事务必须落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    return {"batch_id": batch_id, "sent_count": len(rows)}


async def list_message_batches(
    db: AsyncSession, page: int = 1, page_size: int = 20
) -> dict:
    """人工群发的批次列表（按批次聚合，含送达数与已读数）。

    只列 `batch_id` 非空的批次：内容下架、评论回复这类自动通知是内容操作的副产品，
    不该混在「运营发过什么」的清单里。
    """
    base = select(Message.batch_id).where(Message.batch_id.is_not(None)).group_by(Message.batch_id)
    total = len((await db.execute(base)).scalars().all())

    read_sum = func.sum(case((Message.is_read.is_(True), 1), else_=0))
    rows = (
        await db.execute(
            select(
                Message.batch_id,
                func.min(Message.title).label("title"),
                func.min(Message.content).label("content"),
                func.min(Message.link).label("link"),
                func.count(Message.id).label("sent_count"),
                read_sum.label("read_count"),
                func.min(Message.created_at).label("created_at"),
            )
            .where(Message.batch_id.is_not(None))
            .group_by(Message.batch_id)
            .order_by(func.min(Message.created_at).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [
        {
            "batch_id": r.batch_id,
            "title": r.title,
            "content": r.content,
            "link": r.link,
            "sent_count": int(r.sent_count or 0),
            "read_count": int(r.read_count or 0),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
