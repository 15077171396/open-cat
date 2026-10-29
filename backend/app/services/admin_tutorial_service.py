"""管理后台 - 教程 CRUD 服务

从 admin.py 抽取的 ORM 操作。保持与原 admin 路由完全相同的响应格式，
以维持前端兼容性。
"""

from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only

from app.models.tutorial import Tutorial, TutorialNetdiskLink
from app.services.content_notify import notify_favoriters_unpublished
from app.services.content_purge import purge_content_records
from app.models.user import User
from app.core.exceptions import AppException
from app.services.admin_audit_service import audit_changes, record_admin_action, summarize_ids
from app.utils.sanitize import sanitize_content
from app.services.search import TUTORIAL_SEARCH_COLS, apply_keyword_filter
from app.services.content_cache import invalidate_tutorials
from app.services.content_soft_delete import (
    deleted_condition,
    mark_deleted,
    mark_restored,
)


async def list_tutorials(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str = None,
    category: str = None,
    status: str = None,
    is_published: bool = None,
    access_mode: str = None,
    deleted: str = "active",
) -> dict:
    """教程列表（管理后台分页；支持标题/简介/正文搜索与分类/更新状态/发布状态/阅读权限筛选）

    :param deleted: 回收站视图 ``active``（默认，只看正常内容）/ ``deleted``（只看回收站）/ ``all``
    """
    conditions = []
    # 回收站过滤放在最前：它是「视图」而不是「筛选项」，任何筛选都不该绕过它
    soft_delete_cond = deleted_condition(Tutorial.deleted_at, deleted)
    if soft_delete_cond is not None:
        conditions.append(soft_delete_cond)
    if category and category != "all":
        conditions.append(Tutorial.category == category)
    if status:
        conditions.append(Tutorial.status == status)
    if is_published is not None:
        conditions.append(Tutorial.is_published == is_published)
    if access_mode:
        conditions.append(Tutorial.access_mode == access_mode)

    # 搜索：全文优先、LIKE 兜底（与公开端共用同一构造器，第三轮增量审查 P1-41）
    total = await apply_keyword_filter(db, Tutorial, conditions, TUTORIAL_SEARCH_COLS, keyword, log_search=False)
    # 列表仅用小字段，load_only 避免 content/introduction (MEDIUMTEXT) 大字段传输
    # 封面/简介/学习次数/更新时间供后台列表渲染（缩略图、简介、指标列、最近更新），均为轻量列
    query = (
        select(Tutorial)
        .options(
            load_only(
                Tutorial.id, Tutorial.title, Tutorial.description, Tutorial.category,
                Tutorial.status, Tutorial.is_published, Tutorial.access_mode,
                Tutorial.points_price, Tutorial.is_pinned, Tutorial.is_recommended,
                Tutorial.cover_image, Tutorial.view_count, Tutorial.updated_at,
                Tutorial.published_at,
            )
        )
        .where(*conditions)
        .order_by(Tutorial.created_at.desc(), Tutorial.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tutorials = (await db.execute(query)).scalars().all()

    items = [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "category": t.category,
            "cover_image": t.cover_image,
            "view_count": t.view_count,
            "status": t.status,
            "is_published": t.is_published,
            "access_mode": t.access_mode,
            "points_price": t.points_price,
            "is_pinned": t.is_pinned,
            "is_recommended": t.is_recommended,
            "published_at": t.published_at.isoformat() if t.published_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in tutorials
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_tutorial(db: AsyncSession, tutorial_id: int) -> dict:
    """教程详情（含网盘链接）"""
    result = await db.execute(
        select(Tutorial)
        .options(selectinload(Tutorial.netdisk_links))
        .where(Tutorial.id == tutorial_id)
    )
    tutorial = result.scalar_one_or_none()
    if not tutorial:
        raise AppException(code=404, message="教程不存在")
    return {
        "id": tutorial.id,
        "title": tutorial.title,
        "description": tutorial.description,
        "introduction": tutorial.introduction,
        "category": tutorial.category,
        "content": tutorial.content,
        "cover_image": tutorial.cover_image,
        "view_count": tutorial.view_count,
        "status": tutorial.status,
        "is_published": tutorial.is_published,
        "access_mode": tutorial.access_mode,
        "points_price": tutorial.points_price,
        "is_pinned": tutorial.is_pinned,
        "is_recommended": tutorial.is_recommended,
        "seo_title": tutorial.seo_title,
        "seo_description": tutorial.seo_description,
        "seo_keywords": tutorial.seo_keywords,
        "published_at": tutorial.published_at.isoformat() if tutorial.published_at else None,
        "netdisk_links": [
            {"platform": link.platform, "url": link.url, "extract_code": link.extract_code}
            for link in tutorial.netdisk_links
        ],
    }


def _normalize_access_fields(data: dict) -> dict:
    """归一权限字段：非法模式回退 free；免费模式积分价强制 0（免费教程不售卖）"""
    access_mode = data.get("access_mode") or "free"
    if access_mode not in ("free", "vip"):
        access_mode = "free"
    points_price = int(data.get("points_price") or 0)
    if access_mode == "free":
        points_price = 0
    return {"access_mode": access_mode, "points_price": points_price}


async def create_tutorial(db: AsyncSession, data: dict, admin: User) -> dict:
    """发布教程

    data 字段：title/description/category/content/cover_image/status/access_mode/points_price/is_pinned/is_recommended/netdisk_links
    """
    access = _normalize_access_fields(data)
    tutorial = Tutorial(
        title=data["title"],
        description=data["description"],
        introduction=sanitize_content(data.get("introduction")) or None,
        category=data["category"],
        # 章节化改造：新建不传 content（正文按章存储），DB 列 NOT NULL 故缺省空串
        content=sanitize_content(data.get("content")) or "",
        cover_image=data.get("cover_image"),
        status=data.get("status") or "updating",
        is_published=True,
        access_mode=access["access_mode"],
        points_price=access["points_price"],
        is_pinned=bool(data.get("is_pinned")),
        is_recommended=bool(data.get("is_recommended")),
        seo_title=data.get("seo_title") or None,
        seo_description=data.get("seo_description") or None,
        seo_keywords=data.get("seo_keywords") or None,
        # ARCH-006: 统一 UTC
        published_at=datetime.now(timezone.utc),
    )
    db.add(tutorial)
    await db.flush()

    for link in data.get("netdisk_links") or []:
        db.add(
            TutorialNetdiskLink(
                tutorial_id=tutorial.id,
                platform=link["platform"],
                url=link["url"],
                extract_code=link.get("extract_code"),
            )
        )

    await db.flush()  # 先拿到 generate_id 生成的主键，审计才能归因到具体教程
    record_admin_action(
        db, admin, "content_create",
        target_type="tutorial", target_id=tutorial.id,
        summary=f"新建教程《{tutorial.title}》",
        detail={"category": tutorial.category, "is_published": tutorial.is_published},
    )
    # 显式提交：get_db teardown 的 commit 在响应发送后才执行（事件循环交错），
    # 客户端收到响应后立即发起的查询可能读到未提交数据（表现为需手动刷新）
    await db.commit()
    await invalidate_tutorials()
    await db.refresh(tutorial)
    return {"id": tutorial.id}


async def update_tutorial(
    db: AsyncSession, tutorial_id: int, updates: dict, admin: User
) -> None:
    """更新教程（含网盘链接覆盖）

    updates 字段全部可选；netdisk_links 为非 None 时整体覆盖原链接。
    """
    result = await db.execute(
        select(Tutorial)
        .options(selectinload(Tutorial.netdisk_links))
        .where(Tutorial.id == tutorial_id)
    )
    tutorial = result.scalar_one_or_none()
    if not tutorial:
        raise AppException(code=404, message="教程不存在")

    if updates.get("title") is not None:
        tutorial.title = updates["title"]
    if updates.get("description") is not None:
        tutorial.description = updates["description"]
    if updates.get("introduction") is not None:
        tutorial.introduction = sanitize_content(updates["introduction"]) or None
    if updates.get("category") is not None:
        tutorial.category = updates["category"]
    if updates.get("content") is not None:
        tutorial.content = sanitize_content(updates["content"])
    if updates.get("cover_image") is not None:
        tutorial.cover_image = updates["cover_image"]
    if updates.get("status") is not None:
        tutorial.status = updates["status"]
    # 权限字段联动：切换为免费时积分价归零；切换为会员时若未显式传价则保留原值
    if updates.get("access_mode") is not None or updates.get("points_price") is not None:
        new_mode = updates.get("access_mode") or tutorial.access_mode
        if new_mode not in ("free", "vip"):
            new_mode = "free"
        new_price = tutorial.points_price or 0
        if updates.get("points_price") is not None:
            new_price = int(updates["points_price"])
        if new_mode == "free":
            new_price = 0
        tutorial.access_mode = new_mode
        tutorial.points_price = new_price
    if updates.get("is_pinned") is not None:
        tutorial.is_pinned = bool(updates["is_pinned"])
    if updates.get("is_recommended") is not None:
        tutorial.is_recommended = bool(updates["is_recommended"])
    # SEO 字段允许清空（传空串 = 清除，前台回落默认标题/简介）
    if updates.get("seo_title") is not None:
        tutorial.seo_title = updates["seo_title"] or None
    if updates.get("seo_description") is not None:
        tutorial.seo_description = updates["seo_description"] or None
    if updates.get("seo_keywords") is not None:
        tutorial.seo_keywords = updates["seo_keywords"] or None
    if updates.get("netdisk_links") is not None:
        # 批量删除旧链接（单条 DELETE 语句，替代逐条 delete 的 N 次往返）
        await db.execute(
            delete(TutorialNetdiskLink)
            .where(TutorialNetdiskLink.tutorial_id == tutorial_id)
            .execution_options(synchronize_session=False),
        )
        for link in updates["netdisk_links"]:
            db.add(
                TutorialNetdiskLink(
                    tutorial_id=tutorial.id,
                    platform=link["platform"],
                    url=link["url"],
                    extract_code=link.get("extract_code"),
                )
            )
    record_admin_action(
        db, admin, "content_update",
        target_type="tutorial", target_id=tutorial_id,
        summary=f"编辑教程《{tutorial.title}》",
        detail={"fields": sorted(updates.keys()), "changes": audit_changes(tutorial, updates)},
    )
    await db.commit()
    await invalidate_tutorials()


async def toggle_publish(db: AsyncSession, tutorial_id: int, admin: User) -> dict:
    """切换发布状态，首次发布补 published_at

    ⚠️ 必须排除回收站里的内容：`is_published` 是公开端唯一的可见性开关，
    对一个 `deleted_at` 非空的行置 True，等于让「已删除」的内容重新对访客可见
    （而它同时还挂在回收站里，运营会以为它已经下架了）。
    """
    tutorial = (
        await db.execute(
            select(Tutorial).where(
                Tutorial.id == tutorial_id,
                Tutorial.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not tutorial:
        raise AppException(code=404, message="教程不存在")
    was_published = tutorial.is_published
    tutorial.is_published = not tutorial.is_published
    if was_published and not tutorial.is_published:
        await notify_favoriters_unpublished(
            db, target_type="tutorial", target_id=tutorial.id, title=tutorial.title
        )
    if tutorial.is_published and not tutorial.published_at:
        tutorial.published_at = datetime.now(timezone.utc)
    record_admin_action(
        db, admin, "content_publish",
        target_type="tutorial", target_id=tutorial_id,
        summary=f"{'发布' if tutorial.is_published else '取消发布'}教程《{tutorial.title}》",
        detail={"is_published": tutorial.is_published},
    )
    await db.commit()
    await invalidate_tutorials()
    return {"is_published": tutorial.is_published}


async def delete_tutorial(db: AsyncSession, tutorial_id: int, admin: User) -> None:
    """删除教程 —— **软删除**（进回收站，可恢复）。

    与改造前的差异（这是本函数唯一的行为变化）：
    - 不再物理删除教程行、章节树、网盘链接、积分购买记录 —— 它们保留下来，
      恢复时内容才是完整的（否则「恢复」会得到一篇空教程）；
    - 只打 `deleted_at` 并强制下架（公开端立刻不可见，见 content_soft_delete 说明）；
    - 用户侧记录（收藏 / 学习记录 / 评论）仍按原语义清理：**用户的列表里不会出现
      点开即 404 的死条目**，这一点与改造前完全一致。

    审计行在 commit **之前** add，与删除同事务落库：删除回滚则审计行一起消失。
    """
    tutorial = (
        await db.execute(
            select(Tutorial).where(
                Tutorial.id == tutorial_id,
                # 已在回收站里的不再重复删除：重复删除会让 deleted_at 被刷新，
                # 回收站的「删除时间」就不准了（而它是判断「多久该彻底清」的依据）
                Tutorial.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not tutorial:
        raise AppException(code=404, message="教程不存在")

    # 学习记录 / 收藏 / 评论都只存快照、无外键，不会级联（P1-7/P1-9）：
    # 不同步清理的话，用户「我的学习」「我的收藏」里会留下点开即 404 的死条目。
    # 软删除**仍然清理它们**（与改造前一致）—— 回收站恢复的是内容，不是用户的收藏；
    # 清哪几张表由 core.content_types 的能力标记决定（EXT-07）
    await purge_content_records(db, "tutorial", [tutorial_id])

    # 只打标记 + 下架：章节树 / 网盘链接 / 积分购买记录全部保留，恢复时内容才完整
    mark_deleted(tutorial)
    record_admin_action(
        db, admin, "content_delete",
        target_type="tutorial", target_id=tutorial_id,
        summary=f"删除教程《{tutorial.title}》（已移入回收站，可恢复）",
    )
    await db.commit()
    await invalidate_tutorials()


async def batch_delete_tutorials(db: AsyncSession, tutorial_ids: list[int], admin: User) -> dict:
    """批量删除教程（**软删除**，进回收站；语义与单条 delete_tutorial 完全一致）

    部分 id 已不存在（或已在回收站）时不报错，按实际处理的条数返回。
    章节树 / 网盘链接 / 购买记录全部保留，恢复后内容完整。
    """
    tutorials = (
        await db.execute(
            select(Tutorial).where(
                Tutorial.id.in_(tutorial_ids),
                Tutorial.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not tutorials:
        raise AppException(code=404, message="教程不存在")
    ids = [t.id for t in tutorials]

    # 学习记录 / 收藏 / 评论按批清理，避免留下死链孤儿（P1-7/P1-9，同单条删除）
    await purge_content_records(db, "tutorial", ids)

    for t in tutorials:
        mark_deleted(t)

    record_admin_action(
        db, admin, "content_delete",
        target_type="tutorial",
        summary=f"批量删除 {len(ids)} 篇教程（已移入回收站，可恢复）",
        detail={"count": len(ids), "ids": summarize_ids(ids)},
    )
    await db.commit()
    await invalidate_tutorials()
    return {"deleted": len(ids)}


async def restore_tutorial(
    db: AsyncSession, tutorial_id: int, admin: User, *, republish: bool = False
) -> dict:
    """从回收站恢复教程。

    :param republish: 恢复的同时重新发布（默认恢复为草稿，需要人再确认一次）

    返回 ``{"restored": 1, "republished": bool}``，供前端提示「已恢复为草稿 / 已恢复并发布」。
    """
    tutorial = (
        await db.execute(
            select(Tutorial).where(
                Tutorial.id == tutorial_id,
                Tutorial.deleted_at.isnot(None),
            )
        )
    ).scalar_one_or_none()
    if not tutorial:
        # 不在回收站里（不存在 or 本来正常）→ 404，避免「恢复」变成幂等的无操作
        raise AppException(code=404, message="回收站中没有该教程")

    mark_restored(tutorial, republish=republish)
    record_admin_action(
        db, admin, "content_restore",
        target_type="tutorial", target_id=tutorial_id,
        summary=f"恢复教程《{tutorial.title}》" + ("（并重新发布）" if republish else "（保持草稿）"),
    )
    await db.commit()
    await invalidate_tutorials()
    return {"restored": 1, "republished": republish}


async def batch_restore_tutorials(
    db: AsyncSession, tutorial_ids: list[int], admin: User, *, republish: bool = False
) -> dict:
    """批量从回收站恢复；返回实际恢复条数（不在回收站里的 id 静默跳过）"""
    tutorials = (
        await db.execute(
            select(Tutorial).where(
                Tutorial.id.in_(tutorial_ids),
                Tutorial.deleted_at.isnot(None),
            )
        )
    ).scalars().all()
    if not tutorials:
        raise AppException(code=404, message="回收站中没有这些教程")
    ids = [t.id for t in tutorials]
    for t in tutorials:
        mark_restored(t, republish=republish)

    record_admin_action(
        db, admin, "content_restore",
        target_type="tutorial",
        summary=f"批量恢复 {len(ids)} 篇教程" + ("（并重新发布）" if republish else "（保持草稿）"),
        detail={"count": len(ids), "ids": summarize_ids(ids)},
    )
    await db.commit()
    await invalidate_tutorials()
    return {"restored": len(ids), "republished": republish}


async def batch_publish_tutorials(
    db: AsyncSession, tutorial_ids: list[int], publish: bool, admin: User
) -> dict:
    """批量发布/取消发布；首次发布的补 published_at（同单条 toggle_publish 口径）

    同样排除回收站里的内容（理由见 toggle_publish）。
    """
    tutorials = (
        await db.execute(
            select(Tutorial).where(
                Tutorial.id.in_(tutorial_ids),
                Tutorial.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not tutorials:
        raise AppException(code=404, message="教程不存在")
    now = datetime.now(timezone.utc)
    for t in tutorials:
        was_published = t.is_published
        t.is_published = publish
        # 批量下架同样要通知收藏者（单条一次 IN 查询，批量规模受管理端上限约束）
        if was_published and not publish:
            await notify_favoriters_unpublished(
                db, target_type="tutorial", target_id=t.id, title=t.title
            )
        if publish and not t.published_at:
            t.published_at = now
    record_admin_action(
        db, admin, "content_publish",
        summary=f"批量{'发布' if publish else '取消发布'} {len(tutorials)} 篇教程",
        detail={"ids": summarize_ids([t.id for t in tutorials]), "publish": publish},
    )
    await db.commit()
    await invalidate_tutorials()
    return {"updated": len(tutorials)}
