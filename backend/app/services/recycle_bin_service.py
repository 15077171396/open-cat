"""统一回收站：教程 / 评论 / 用户三类软删除记录的聚合、恢复与到期清理。

设计取舍
--------
- **入口统一，动作不复制**：列表和计数在这里聚合，恢复仍调用各业务域自己的服务
  （教程批量恢复、评论恢复、用户恢复），避免出现第二套「恢复后该是什么状态」的规则。
- **权限在服务端裁剪**：汇总只返回当前管理员有查看权限的类型；恢复按钮按各自
  `*:delete` / `user:restore` 权限点亮。没有权限的类型不会出现在页面上。
- **保留期是产品决策，不是随手写的数字**：教程 90 天、评论 30 天；用户账号不自动清理。
  有购买记录的教程也不自动清理（删除付费内容会破坏用户已购买权益）。
- 自动清理只硬删除**已过保留期且确定不会破坏权益**的数据；教程先清章节/网盘链接，
  再删主行，避免外键挡住。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from loguru import logger
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import DataScope, apply_scope
from app.core.exceptions import AppException
from app.models.comment import ContentComment
from app.models.tutorial import Tutorial, TutorialNetdiskLink, TutorialPurchase
from app.models.tutorial_node import TutorialNode
from app.models.user import User
from app.services import admin_user_service, comment_service, rbac_service
from app.services.admin_audit_service import record_admin_action, summarize_ids
from app.services.content_cache import invalidate_tutorials
from app.services.content_purge import purge_content_records
from app.services.content_soft_delete import mark_restored
from app.utils.sanitize import escape_like

TUTORIAL_RETENTION_DAYS = 90
COMMENT_RETENTION_DAYS = 30

_TYPE_LABELS: dict[str, str] = {
    "tutorial": "教程",
    "comment": "评论",
    "user": "用户",
}
_VIEW_PERMS: dict[str, str] = {
    "tutorial": "tutorial:view",
    "comment": "comment:view",
    "user": "user:view",
}
_RESTORE_PERMS: dict[str, str] = {
    "tutorial": "tutorial:delete",  # 与现有「能删才放回」口径一致
    "comment": "comment:delete",
    "user": "user:restore",
}
_RETENTION_DAYS: dict[str, int | None] = {
    "tutorial": TUTORIAL_RETENTION_DAYS,
    "comment": COMMENT_RETENTION_DAYS,
    "user": None,
}


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _expiry(
    deleted_at: datetime | None, retention_days: int | None
) -> tuple[str | None, int | None]:
    """返回 (到期时间 ISO, 剩余天数)。remaining=0 表示已到期、等待清理。"""
    if deleted_at is None or retention_days is None:
        return None, None
    expires = deleted_at + timedelta(days=retention_days)
    remaining = math.ceil((expires - _now_naive_utc()).total_seconds() / 86400)
    return expires.isoformat(), max(0, remaining)


def _item(
    *,
    item_type: str,
    item_id: int,
    title: str,
    subtitle: str,
    deleted_at: datetime | None,
    can_restore: bool,
    retention_days: int | None,
    retention_note: str = "",
) -> dict:
    expires_at, remaining_days = _expiry(deleted_at, retention_days)
    return {
        "type": item_type,
        "type_label": _TYPE_LABELS[item_type],
        "id": item_id,
        "title": title,
        "subtitle": subtitle,
        "deleted_at": deleted_at.isoformat() if deleted_at else None,
        "retention_days": retention_days,
        "expires_at": expires_at,
        "remaining_days": remaining_days,
        "retention_note": retention_note,
        "can_restore": can_restore,
    }


async def _permissions(db: AsyncSession, admin: User) -> tuple[bool, set[str]]:
    rbac = await rbac_service.get_user_rbac(db, admin.id)
    return await rbac_service.is_super_admin(rbac), set(rbac.get("perms") or [])


def _can(is_super: bool, perms: set[str], code: str) -> bool:
    return is_super or code in perms


async def _count_deleted(db: AsyncSession, item_type: str, scope: DataScope | None = None) -> int:
    if item_type == "tutorial":
        conditions = [Tutorial.deleted_at.isnot(None)]
        model = Tutorial
    elif item_type == "comment":
        conditions = [ContentComment.deleted_at.isnot(None)]
        model = ContentComment
    else:
        conditions = [User.deleted_at.isnot(None)]
        model = User
        apply_scope(conditions, scope, owner_col=User.id, dept_col=User.department_id)
    return int((await db.execute(select(func.count(model.id)).where(*conditions))).scalar() or 0)


async def get_summary(
    db: AsyncSession, admin: User, scope: DataScope | None = None
) -> dict:
    """各类型计数 + 保留期；只返回有查看权限的类型。"""
    is_super, perms = await _permissions(db, admin)
    items = []
    for item_type in ("tutorial", "comment", "user"):
        if not _can(is_super, perms, _VIEW_PERMS[item_type]):
            continue
        items.append(
            {
                "type": item_type,
                "type_label": _TYPE_LABELS[item_type],
                "count": await _count_deleted(db, item_type, scope),
                "retention_days": _RETENTION_DAYS[item_type],
                "can_restore": _can(is_super, perms, _RESTORE_PERMS[item_type]),
            }
        )
    return {"total": sum(item["count"] for item in items), "items": items}


async def list_tutorials(
    db: AsyncSession,
    admin: User,
    page: int,
    page_size: int,
    keyword: str | None = None,
) -> dict:
    is_super, perms = await _permissions(db, admin)
    conditions = [Tutorial.deleted_at.isnot(None)]
    if keyword:
        conditions.append(Tutorial.title.contains(escape_like(keyword), escape="\\"))
    total = int((await db.execute(select(func.count(Tutorial.id)).where(*conditions))).scalar() or 0)
    rows = (
        await db.execute(
            select(Tutorial.id, Tutorial.title, Tutorial.category, Tutorial.deleted_at)
            .where(*conditions)
            .order_by(Tutorial.deleted_at.desc(), Tutorial.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    purchased: set[int] = set()
    ids = [row[0] for row in rows]
    if ids:
        purchased = set(
            (
                await db.execute(
                    select(TutorialPurchase.tutorial_id).where(
                        TutorialPurchase.tutorial_id.in_(ids)
                    )
                )
            ).scalars().all()
        )
    can_restore = _can(is_super, perms, _RESTORE_PERMS["tutorial"])
    items = []
    for tutorial_id, title, category, deleted_at in rows:
        has_purchase = tutorial_id in purchased
        items.append(
            _item(
                item_type="tutorial",
                item_id=tutorial_id,
                title=title,
                subtitle=category or "未分类",
                deleted_at=deleted_at,
                can_restore=can_restore,
                # 有购买记录 = 删掉会破坏用户已购买的权益，保留期锁定
                retention_days=None if has_purchase else TUTORIAL_RETENTION_DAYS,
                retention_note="有购买记录，不自动清理" if has_purchase else "",
            )
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def list_comments(
    db: AsyncSession,
    admin: User,
    page: int,
    page_size: int,
    keyword: str | None = None,
) -> dict:
    is_super, perms = await _permissions(db, admin)
    data = await comment_service.admin_list_comments(
        db, page, page_size, keyword=keyword, deleted_only=True
    )
    can_restore = _can(is_super, perms, _RESTORE_PERMS["comment"])
    items = []
    for row in data["items"]:
        target = row.get("target_title") or f"{row.get('target_type')} #{row.get('target_id')}"
        author = row.get("author_nickname") or "匿名用户"
        prefix = "回复" if row.get("parent_id") else "评论"
        items.append(
            _item(
                item_type="comment",
                item_id=row["id"],
                title=row.get("content") or "（空评论）",
                subtitle=f"{prefix} · {author} · {target}",
                deleted_at=_parse_time(row.get("deleted_at")),
                can_restore=can_restore,
                retention_days=COMMENT_RETENTION_DAYS,
            )
        )
    return {
        "items": items,
        "total": data["total"],
        "page": data["page"],
        "page_size": data["page_size"],
    }


async def list_users(
    db: AsyncSession,
    admin: User,
    page: int,
    page_size: int,
    keyword: str | None = None,
    scope: DataScope | None = None,
) -> dict:
    is_super, perms = await _permissions(db, admin)
    data = await admin_user_service.list_users(
        db, page, page_size, keyword=keyword, account_status="deleted", scope=scope
    )
    can_restore = _can(is_super, perms, _RESTORE_PERMS["user"])
    items = []
    for row in data["items"]:
        role = row.get("role") or "user"
        department = row.get("department_name")
        subtitle = f"角色：{role}" + (f" · {department}" if department else "")
        items.append(
            _item(
                item_type="user",
                item_id=row["id"],
                title=row.get("account") or f"用户 #{row['id']}",
                subtitle=subtitle,
                deleted_at=_parse_time(row.get("deleted_at")),
                can_restore=can_restore,
                retention_days=None,
                retention_note="账号不自动清理",
            )
        )
    return {
        "items": items,
        "total": data["total"],
        "page": data["page"],
        "page_size": data["page_size"],
    }


async def restore_tutorials(db: AsyncSession, ids: list[int], admin: User) -> dict:
    """恢复教程（与教程页的批量恢复同一语义：恢复为草稿，不自动重新发布）。

    这里不转调 `admin_tutorial_service.batch_restore_tutorials`：那个函数自带
    审计与 commit，转调会变成「业务提交后再补一条审计」的两个事务。回收站把
    「恢复 + 审计」放在同一事务里，和删除路径保持一致。
    """
    rows = (
        await db.execute(
            select(Tutorial).where(
                Tutorial.id.in_(ids), Tutorial.deleted_at.isnot(None)
            )
        )
    ).scalars().all()
    if not rows:
        raise AppException(code=404, message="回收站中没有这些教程")
    for tutorial in rows:
        mark_restored(tutorial)
    record_admin_action(
        db,
        admin,
        "content_restore",
        target_type="tutorial",
        summary=f"从回收站恢复 {len(rows)} 篇教程（保持草稿）",
        detail={"count": len(rows), "ids": summarize_ids([row.id for row in rows])},
    )
    await db.commit()
    await invalidate_tutorials()
    return {"restored": len(rows)}


async def restore_comments(db: AsyncSession, ids: list[int], admin: User) -> dict:
    rows = (
        await db.execute(
            select(ContentComment).where(
                ContentComment.id.in_(ids), ContentComment.deleted_at.isnot(None)
            )
        )
    ).scalars().all()
    if not rows:
        raise AppException(code=404, message="回收站中没有这些评论")

    # 删除顶层评论时会连带软删回复；恢复顶层时把同一时刻被连带的回复一起还原，
    # 但不碰「后来单独删掉」的回复（deleted_at 不同）。
    parents = {row.id: row for row in rows if row.parent_id is None}
    if parents:
        replies = (
            await db.execute(
                select(ContentComment).where(
                    ContentComment.parent_id.in_(list(parents)),
                    ContentComment.deleted_at.isnot(None),
                )
            )
        ).scalars().all()
        row_ids = {row.id for row in rows}
        for reply in replies:
            parent = parents.get(reply.parent_id)
            if parent is not None and reply.deleted_at == parent.deleted_at and reply.id not in row_ids:
                rows.append(reply)
                row_ids.add(reply.id)

    for row in rows:
        row.deleted_at = None
    record_admin_action(
        db,
        admin,
        "comment_restore",
        target_type="comment",
        summary=f"从回收站恢复 {len(rows)} 条评论",
        detail={"count": len(rows), "ids": summarize_ids([row.id for row in rows])},
    )
    await db.commit()
    return {"restored": len(rows)}


async def restore_users(db: AsyncSession, ids: list[int], admin: User) -> dict:
    rows = (
        await db.execute(
            select(User).where(User.id.in_(ids), User.deleted_at.isnot(None))
        )
    ).scalars().all()
    if not rows:
        raise AppException(code=404, message="回收站中没有这些用户")
    for user in rows:
        user.deleted_at = None
    record_admin_action(
        db,
        admin,
        "user_restore",
        target_type="user",
        summary=f"从回收站恢复 {len(rows)} 个用户",
        detail={"count": len(rows), "ids": summarize_ids([user.id for user in rows])},
    )
    await db.commit()
    return {"restored": len(rows)}


async def purge_expired(db: AsyncSession, *, now: datetime | None = None) -> dict:
    """硬删除超过保留期的教程 / 评论；**不提交**，由调度器在成功后统一提交。

    用户账号不自动清理；有购买记录的教程跳过（删掉会破坏已购买的权益）。
    """
    current = (now or _now_naive_utc())
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)

    comment_cutoff = current - timedelta(days=COMMENT_RETENTION_DAYS)
    comment_result = await db.execute(
        delete(ContentComment).where(
            ContentComment.deleted_at.isnot(None),
            ContentComment.deleted_at < comment_cutoff,
        )
    )
    comments_deleted = int(comment_result.rowcount or 0)

    tutorial_cutoff = current - timedelta(days=TUTORIAL_RETENTION_DAYS)
    candidate_ids = (
        await db.execute(
            select(Tutorial.id).where(
                Tutorial.deleted_at.isnot(None), Tutorial.deleted_at < tutorial_cutoff
            )
        )
    ).scalars().all()
    purchased: set[int] = set()
    if candidate_ids:
        purchased = set(
            (
                await db.execute(
                    select(TutorialPurchase.tutorial_id).where(
                        TutorialPurchase.tutorial_id.in_(candidate_ids)
                    )
                )
            ).scalars().all()
        )
    eligible = [tutorial_id for tutorial_id in candidate_ids if tutorial_id not in purchased]

    if eligible:
        # 先清用户侧关联，再删章节 / 网盘链接，最后删主行；顺序错会被外键挡住
        await purge_content_records(db, "tutorial", eligible)
        # 章节树有自关联外键：先把所有节点的 parent_id 置空，再整批删除
        await db.execute(
            update(TutorialNode)
            .where(TutorialNode.tutorial_id.in_(eligible))
            .values(parent_id=None)
        )
        await db.execute(delete(TutorialNode).where(TutorialNode.tutorial_id.in_(eligible)))
        await db.execute(
            delete(TutorialNetdiskLink).where(TutorialNetdiskLink.tutorial_id.in_(eligible))
        )
        await db.execute(delete(Tutorial).where(Tutorial.id.in_(eligible)))

    if comments_deleted or eligible or purchased:
        logger.bind(
            event="recycle_bin_purge",
            tutorials=len(eligible),
            comments=comments_deleted,
            skipped_purchased=len(purchased),
        ).info(
            f"回收站到期清理：教程 {len(eligible)} 篇 / 评论 {comments_deleted} 条"
            f"（有购买记录跳过 {len(purchased)} 篇）"
        )
    return {
        "tutorials": len(eligible),
        "comments": comments_deleted,
        "tutorials_skipped": len(purchased),
    }
