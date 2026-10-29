"""管理后台 - 平台公告 CRUD 服务

从 admin.py 抽取的 ORM 操作。公告为纯文本短内容（非富文本编辑器产出），
入库前统一经 sanitize_content 过滤，防止存储型 XSS。
"""

from datetime import datetime, timezone

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.announcement import Announcement
from app.models.user import User
from app.core.exceptions import AppException
from app.utils.sanitize import escape_like, sanitize_content
from app.services.admin_audit_service import audit_changes, record_admin_action, summarize_ids
from app.services.content_cache import invalidate_home


async def _invalidate_home_cache() -> None:
    """公告变更后失效首页聚合缓存（键定义与失效逻辑集中在 content_cache）"""
    await invalidate_home()


def _to_dict(a: Announcement) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "content": a.content,
        "tag": a.tag,
        "color": a.color,
        "is_published": a.is_published,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "publish_at": a.publish_at.isoformat() if a.publish_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


async def admin_list_announcements(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 10,
    keyword: str = None,
    is_published: bool = None,
    tag: str = None,
) -> dict:
    """公告列表（含未发布，供管理端查看全部状态）"""
    conditions = []
    if keyword:
        conditions.append(
            Announcement.title.contains(escape_like(keyword), escape="\\")
        )
    if is_published is not None:
        conditions.append(Announcement.is_published == is_published)
    if tag:
        conditions.append(Announcement.tag == tag)

    total = (
        await db.execute(
            select(func.count(Announcement.id)).where(*conditions)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(Announcement)
            .where(*conditions)
            .order_by(Announcement.is_published.desc(), Announcement.created_at.desc(), Announcement.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [_to_dict(a) for a in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _to_naive_utc(value) -> datetime | None:
    """把前端传来的时间统一成 naive UTC（全库约定）。

    - 带时区的 ISO（`2026-10-27T10:00:00+08:00`）→ 转 UTC 再去掉 tzinfo；
    - 不带时区的（`2026-10-27T10:00:00`，DatePicker 常见）→ 视为 UTC 原样保留。
      刻意**不猜本地时区**：服务端时区恒为 UTC（compose 已锁定），猜错会整体偏 8 小时。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return None


async def admin_create_announcement(db: AsyncSession, admin: User, data: dict) -> dict:
    """创建公告

    data 字段：title/content/tag/color/is_published

    公告直接决定全站首屏展示内容，写入管理操作审计（第二轮增量审查 P2：
    此前公告增删改**全链路零审计**，误删/误撤无可追溯）。
    """
    a = Announcement(
        title=data["title"],
        content=sanitize_content(data.get("content") or ""),
        tag=data.get("tag") or "公告",
        color=data.get("color") or "#3b82f6",
        is_published=bool(data.get("is_published", True)),
        published_at=(
            datetime.now(timezone.utc) if data.get("is_published", True) else None
        ),
        publish_at=_to_naive_utc(data.get("publish_at")),
    )
    db.add(a)
    # 先 flush 拿到 generate_id 生成的主键，审计才能归因到具体公告
    await db.flush()
    record_admin_action(
        db, admin, "announcement_create",
        target_type="announcement", target_id=a.id,
        summary=f"新建公告「{a.title[:30]}」",
        detail={
            "is_published": a.is_published,
            "tag": a.tag,
            "publish_at": a.publish_at.isoformat() if a.publish_at else None,
        },
    )
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    if a.is_published:
        await _invalidate_home_cache()
    return {"id": a.id}


async def admin_update_announcement(
    db: AsyncSession, admin: User, announcement_id: int, updates: dict
) -> None:
    """更新公告

    首次从草稿转为发布时补记 published_at（已有值则保留原发布时间）。
    """
    a = (
        await db.execute(
            select(Announcement).where(Announcement.id == announcement_id)
        )
    ).scalar_one_or_none()
    if not a:
        raise AppException(code=404, message="公告不存在")

    if updates.get("title") is not None:
        a.title = updates["title"]
    if updates.get("content") is not None:
        a.content = sanitize_content(updates["content"])
    if updates.get("tag") is not None:
        a.tag = updates["tag"]
    if updates.get("color") is not None:
        a.color = updates["color"]
    # 定时发布：显式传 null 表示取消定时（立即生效），所以用 `in` 判断而不是 `is not None`
    if "publish_at" in updates:
        a.publish_at = _to_naive_utc(updates.get("publish_at"))
    if updates.get("is_published") is not None:
        was_published = a.is_published
        if updates["is_published"] and not a.is_published and not a.published_at:
            a.published_at = datetime.now(timezone.utc)
        a.is_published = bool(updates["is_published"])
    else:
        was_published = a.is_published
    record_admin_action(
        db, admin, "announcement_update",
        target_type="announcement", target_id=announcement_id,
        summary=f"编辑公告「{a.title[:30]}」",
        detail={"fields": sorted(updates.keys()), "changes": audit_changes(a, updates)},
    )
    await db.commit()
    # 状态翻转（发布/撤下）或改了定时时间，首页公告条都要同步变化 ——
    # 否则「把定时改成现在」后首页仍是旧缓存，看起来像没生效
    if a.is_published or was_published or "publish_at" in updates:
        await _invalidate_home_cache()


async def admin_delete_announcement(
    db: AsyncSession, admin: User, announcement_id: int
) -> None:
    """删除公告（硬删除）"""
    a = (
        await db.execute(
            select(Announcement).where(Announcement.id == announcement_id)
        )
    ).scalar_one_or_none()
    if not a:
        raise AppException(code=404, message="公告不存在")
    # 先取标量：delete 后对象进入 deleted 状态，不应再依赖其属性
    title, was_published = a.title, a.is_published
    await db.delete(a)
    record_admin_action(
        db, admin, "announcement_delete",
        target_type="announcement", target_id=announcement_id,
        summary=f"删除公告「{title[:30]}」",
        detail={"was_published": was_published},
    )
    await db.commit()
    if was_published:
        await _invalidate_home_cache()


async def admin_batch_delete_announcements(
    db: AsyncSession, admin: User, announcement_ids: list[int]
) -> dict:
    """批量删除公告（硬删除，单条 DELETE 按批合并）

    部分 id 已不存在时不报错，按实际存在的删除并返回数量。
    """
    # 删除前记录是否存在已发布公告：命中则需失效首页缓存
    had_published = (
        await db.execute(
            select(func.count(Announcement.id)).where(
                Announcement.id.in_(announcement_ids),
                Announcement.is_published == True,  # noqa
            )
        )
    ).scalar()
    result = await db.execute(
        delete(Announcement)
        .where(Announcement.id.in_(announcement_ids))
        .execution_options(synchronize_session=False),
    )
    await db.commit()
    deleted = result.rowcount or 0
    if not deleted:
        raise AppException(code=404, message="公告不存在")
    record_admin_action(
        db, admin, "announcement_delete",
        target_type="announcement",
        summary=f"批量删除 {deleted} 条公告",
        detail={"count": deleted, "ids": summarize_ids(announcement_ids)},
    )
    await db.commit()
    if had_published:
        await _invalidate_home_cache()
    return {"deleted": deleted}


async def admin_batch_publish_announcements(
    db: AsyncSession, admin: User, announcement_ids: list[int], publish: bool
) -> dict:
    """批量发布 / 撤下公告

    草稿首次发布时补记 published_at（已有值则保留原发布时间）。
    状态发生翻转时失效首页公告条缓存。
    """
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(Announcement).where(Announcement.id.in_(announcement_ids))
        )
    ).scalars().all()
    if not rows:
        raise AppException(code=404, message="公告不存在")
    # 草稿转发布且尚无发布时间的才补记；已发布过的保留原发布时间
    to_stamp = (
        select(Announcement.id)
        .where(
            Announcement.id.in_(announcement_ids),
            Announcement.is_published == False,  # noqa
            Announcement.published_at.is_(None),
        )
    )
    stamp_ids = set((await db.execute(to_stamp)).scalars().all())

    updated = 0
    for a in rows:
        if a.is_published != publish:
            a.is_published = publish
            updated += 1
        if publish and a.id in stamp_ids:
            a.published_at = now
    record_admin_action(
        db, admin, "announcement_publish",
        target_type="announcement",
        summary=f"{'发布' if publish else '撤下'} {updated} 条公告",
        detail={"publish": publish, "updated": updated, "ids": summarize_ids(announcement_ids)},
    )
    await db.commit()
    if updated:
        await _invalidate_home_cache()
    return {"updated": updated}
