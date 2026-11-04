"""站内聊天服务层：会话、消息、未读角标。

设计口径（与 FastapiAdmin 的 chat 模块同定位）：
- 轻量内部沟通：纯文字、私聊（direct，两人、幂等创建）与群聊（group，≥3 人）；
- 未读 = 「消息 id > 我的 last_read_id 且不是我发的」计数 —— 消息主键自增是游标
  可比较的前提（见 models/chat.py 模块注释）；
- 不做文件/撤回/已读回执/多端同步；成员建群后固定（改群成员是下一个迭代的事）；
- 不写管理操作审计：个人通信不是业务数据变更，消息表本身就是台账。

WS 推送不在本层：服务层只管落库与读模型，连接管理与扇出在 api/admin_chat.py。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.chat import (
    CHAT_MESSAGE_MAX_LENGTH,
    CHAT_GROUP_MIN_MEMBERS,
    ChatConversation,
    ChatMember,
    ChatMessage,
)
from app.models.user import User
from app.utils.sanitize import sanitize_plain_text
from app.utils.time import utcnow

_CONVERSATION_TYPES = frozenset({"direct", "group"})


def message_to_dict(m: ChatMessage) -> dict:
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "sender_id": m.sender_id,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


async def load_ws_user(db: AsyncSession, user_id: int) -> User | None:
    """WS 握手鉴权的用户加载（未删除 + 必须 admin）。

    放在服务层而不是 api/admin_chat.py：架构守卫要求路由层不直接写 ORM。
    """
    user = (
        await db.scalars(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
    ).first()
    if user is None or user.role != "admin":
        return None
    return user


async def _validate_members(db: AsyncSession, user_ids: list[int]) -> list[User]:
    """校验目标成员都是未删除的有效账号（聊天的对象是管理员，谁入库谁说了算）。"""
    unique_ids = list(dict.fromkeys(user_ids))
    rows = (
        await db.scalars(
            select(User).where(
                User.id.in_(unique_ids),
                User.deleted_at.is_(None),
            )
        )
    ).all()
    found = {row.id for row in rows}
    missing = [uid for uid in unique_ids if uid not in found]
    if missing:
        raise AppException(code=404, message=f"用户不存在或已注销: {missing}")
    return rows


async def _member_ids(db: AsyncSession, conversation_id: int) -> list[int]:
    rows = (
        await db.scalars(
            select(ChatMember.user_id).where(ChatMember.conversation_id == conversation_id)
        )
    ).all()
    return list(rows)


async def _require_member(db: AsyncSession, conversation_id: int, user_id: int) -> None:
    """不是成员的会话一律 404：不暴露「会话存在但你不在」的信息。"""
    row = (
        await db.scalars(
            select(ChatMember).where(
                ChatMember.conversation_id == conversation_id,
                ChatMember.user_id == user_id,
            )
        )
    ).first()
    if row is None:
        raise AppException(code=404, message="会话不存在")


async def get_or_create_direct(db: AsyncSession, me: User, other_id: int) -> dict:
    """发起/获取单聊（幂等）：同一对管理员永远只有一个 direct 会话。"""
    if other_id == me.id:
        raise AppException(code=400, message="不能和自己发起单聊")
    others = await _validate_members(db, [other_id])
    other = others[0]
    if other.role != "admin":
        raise AppException(code=400, message="站内聊天仅限管理员之间")

    # 幂等：按「direct 会话 + 恰好这两个成员」找现有会话
    # （多列查询不能用 scalars——它只解出第一列；取整个 ORM 实体在 Python 侧配对）
    conv_ids = (
        await db.scalars(
            select(ChatMember.conversation_id).where(ChatMember.user_id == me.id)
        )
    ).all()
    if conv_ids:
        member_rows = (
            await db.scalars(
                select(ChatMember).where(
                    ChatMember.conversation_id.in_(list(conv_ids)),
                    ChatMember.user_id.in_([me.id, other_id]),
                )
            )
        ).all()
        counts: dict[int, set[int]] = {}
        for member in member_rows:
            counts.setdefault(member.conversation_id, set()).add(member.user_id)
        for conv_id, members in counts.items():
            if members == {me.id, other_id} and len(members) == 2:
                return {"id": conv_id, "created": False}

    conv = ChatConversation(type="direct", title=None, created_by=me.id)
    db.add(conv)
    await db.flush()
    db.add(ChatMember(conversation_id=conv.id, user_id=me.id))
    db.add(ChatMember(conversation_id=conv.id, user_id=other_id))
    await db.commit()
    return {"id": conv.id, "created": True}


async def create_group(db: AsyncSession, me: User, title: str, member_ids: list[int]) -> dict:
    """建群：标题必填，成员（含发起人）去重后 ≥3 人；成员建群后固定。"""
    title = (title or "").strip()
    if not title:
        raise AppException(code=400, message="群聊标题不能为空")
    if len(title) > 100:
        raise AppException(code=400, message="群聊标题不能超过 100 字")

    users = await _validate_members(db, [me.id, *member_ids])
    if any(u.role != "admin" for u in users):
        raise AppException(code=400, message="站内聊天仅限管理员之间")
    unique_ids = list(dict.fromkeys([me.id, *member_ids]))
    if len(unique_ids) < CHAT_GROUP_MIN_MEMBERS:
        raise AppException(code=400, message=f"群聊至少需要 {CHAT_GROUP_MIN_MEMBERS} 名成员（两人请用单聊）")

    conv = ChatConversation(type="group", title=title, created_by=me.id)
    db.add(conv)
    await db.flush()
    for uid in unique_ids:
        db.add(ChatMember(conversation_id=conv.id, user_id=uid))
    await db.commit()
    return {"id": conv.id, "created": True}


async def _conversation_payload(
    db: AsyncSession, conv: ChatConversation, me_id: int
) -> dict:
    """单条会话的展示载荷：最后一条消息、未读数、展示名（单聊取对方）。"""
    members = (
        await db.scalars(
            select(ChatMember).where(ChatMember.conversation_id == conv.id)
        )
    ).all()
    member_map = {member.user_id: member.last_read_id for member in members}
    my_last_read = member_map.get(me_id, 0)
    unread = 0
    if conv.id is not None:
        unread = (
            await db.scalar(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == conv.id,
                    ChatMessage.id > my_last_read,
                    ChatMessage.sender_id != me_id,
                )
            )
        ) or 0

    last = None
    last_row = (
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.id.desc())
            .limit(1)
        )
    ).first()
    if last_row is not None:
        last = message_to_dict(last_row)

    # 展示名：群聊用标题；单聊用对方账号/昵称
    title = conv.title or ""
    other_ids = [uid for uid in member_map if uid != me_id]
    profiles: dict[int, dict] = {}
    if other_ids:
        user_rows = (
            await db.scalars(
                select(User).where(User.id.in_(other_ids), User.deleted_at.is_(None))
            )
        ).all()
        profiles = {
            u.id: {"id": u.id, "account": u.account, "nickname": u.nickname}
            for u in user_rows
        }
    if conv.type == "direct" and other_ids:
        peer = profiles.get(other_ids[0])
        title = (peer or {}).get("nickname") or (peer or {}).get("account") or f"用户 {other_ids[0]}"

    return {
        "id": conv.id,
        "type": conv.type,
        "title": title,
        "member_ids": list(member_map.keys()),
        "profiles": list(profiles.values()),
        "last_message": last,
        "unread": unread,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }


async def list_conversations(db: AsyncSession, me: User) -> dict:
    """我的会话列表（按最近消息时间倒序），供聊天页左栏与未读角标。"""
    conv_ids = (
        await db.scalars(
            select(ChatMember.conversation_id).where(ChatMember.user_id == me.id)
        )
    ).all()
    if not conv_ids:
        return {"items": [], "unread_total": 0}
    convs = (
        await db.scalars(
            select(ChatConversation)
            .where(ChatConversation.id.in_(list(conv_ids)))
            .order_by(ChatConversation.updated_at.desc())
        )
    ).all()
    items = [await _conversation_payload(db, conv, me.id) for conv in convs]
    items.sort(key=lambda item: item["updated_at"] or "", reverse=True)
    return {"items": items, "unread_total": sum(item["unread"] for item in items)}


async def unread_total(db: AsyncSession, me: User) -> int:
    """全站未读总数（顶栏角标）。与列表页的逐会话计数同一口径。"""
    conv_ids = (
        await db.scalars(
            select(ChatMember.conversation_id).where(ChatMember.user_id == me.id)
        )
    ).all()
    if not conv_ids:
        return 0
    reads = (
        await db.scalars(
            select(ChatMember).where(
                ChatMember.user_id == me.id,
                ChatMember.conversation_id.in_(list(conv_ids)),
            )
        )
    ).all()
    total = 0
    for member in reads:
        unread = (
            await db.scalar(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == member.conversation_id,
                    ChatMessage.id > member.last_read_id,
                    ChatMessage.sender_id != me.id,
                )
            )
        ) or 0
        total += unread
    return total


async def list_messages(
    db: AsyncSession, me: User, conversation_id: int, *, before_id: int | None, limit: int = 50
) -> dict:
    """历史消息（游标分页）：取 before_id 之前的最新 limit 条，**正序**返回。"""
    await _require_member(db, conversation_id, me.id)
    conditions = [ChatMessage.conversation_id == conversation_id]
    if before_id is not None:
        conditions.append(ChatMessage.id < before_id)
    rows = (
        await db.scalars(
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
    ).all()
    rows = list(reversed(rows))
    has_more = bool(rows) and rows[0].id > (
        await db.scalar(
            select(func.min(ChatMessage.id)).where(
                ChatMessage.conversation_id == conversation_id
            )
        )
        or 0
    )
    return {
        "items": [message_to_dict(m) for m in rows],
        "has_more": has_more,
    }


async def send_message(db: AsyncSession, me: User, conversation_id: int, content: str) -> dict:
    """发消息（成员校验 + 清洗 + 落库）。扇出由 API 层的连接管理负责。"""
    await _require_member(db, conversation_id, me.id)
    content = (content or "").strip()
    if not content:
        raise AppException(code=400, message="消息内容不能为空")
    if len(content) > CHAT_MESSAGE_MAX_LENGTH:
        raise AppException(code=400, message=f"消息不能超过 {CHAT_MESSAGE_MAX_LENGTH} 字")
    # 纯文本净化：剥离全部 HTML 标签防存储型 XSS（前端按 text 渲染，标签无意义）
    content = sanitize_plain_text(content) or content.strip()

    message = ChatMessage(conversation_id=conversation_id, sender_id=me.id, content=content)
    db.add(message)
    # 发消息即已读：不然自己刚发的消息会在别人回复前一直占着未读位
    member = (
        await db.scalars(
            select(ChatMember).where(
                ChatMember.conversation_id == conversation_id,
                ChatMember.user_id == me.id,
            )
        )
    ).first()
    await db.flush()
    if member is not None:
        member.last_read_id = message.id
    conv = (
        await db.scalars(
            select(ChatConversation).where(ChatConversation.id == conversation_id)
        )
    ).first()
    if conv is not None:
        conv.updated_at = utcnow().replace(tzinfo=None)
    # created_at 是 server_default 列：flush 落行 + refresh 取回后再序列化
    # （INSERT 后直接读未加载列，真实异步会话抛 MissingGreenlet）
    await db.flush()
    await db.refresh(message)
    await db.commit()
    return message_to_dict(message)


async def mark_read(db: AsyncSession, me: User, conversation_id: int) -> dict:
    """标记已读：游标推进到当前会话最大消息 id（进入聊天页 / 收到推送时调用）。"""
    await _require_member(db, conversation_id, me.id)
    max_id = (
        await db.scalar(
            select(func.max(ChatMessage.id)).where(
                ChatMessage.conversation_id == conversation_id
            )
        )
    ) or 0
    member = (
        await db.scalars(
            select(ChatMember).where(
                ChatMember.conversation_id == conversation_id,
                ChatMember.user_id == me.id,
            )
        )
    ).first()
    if member is not None and member.last_read_id < max_id:
        member.last_read_id = max_id
        await db.commit()
    return {"conversation_id": conversation_id, "last_read_id": max_id}
