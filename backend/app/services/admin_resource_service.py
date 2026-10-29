"""管理后台 - 资源 CRUD 服务

从 admin.py 抽取的 ORM 操作。保持与原 admin 路由完全相同的响应格式。
"""

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only

from app.models.resource import Resource, ResourceNetdiskLink
from app.services.content_notify import notify_favoriters_unpublished
from app.services.content_purge import purge_content_records
from app.models.user import User
from app.core.exceptions import AppException
from app.services.admin_audit_service import audit_changes, record_admin_action, summarize_ids
from app.services.content_author import author_fields, fill_author_if_absent
from app.utils.sanitize import sanitize_content
from app.services.search import RESOURCE_SEARCH_COLS, apply_keyword_filter
from app.services.content_cache import invalidate_resources
from app.services.content_soft_delete import (
    deleted_condition,
    mark_deleted,
    mark_restored,
)


async def list_resources(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str = None,
    resource_type: str = None,
    is_featured: bool = None,
    deleted: str = "active",
) -> dict:
    """资源列表（含分页；支持名称/简介/正文搜索与类型/精选筛选）

    :param deleted: 回收站视图 ``active``（默认）/ ``deleted``（只看回收站）/ ``all``
    """
    conditions = []
    # 回收站过滤是「视图」不是「筛选项」：任何筛选都不该把它绕过
    soft_delete_cond = deleted_condition(Resource.deleted_at, deleted)
    if soft_delete_cond is not None:
        conditions.append(soft_delete_cond)
    if resource_type:
        conditions.append(Resource.resource_type == resource_type)
    if is_featured is not None:
        conditions.append(Resource.is_featured == is_featured)

    # 搜索：全文优先、LIKE 兜底（与公开端共用同一构造器，第三轮增量审查 P1-41）
    total = await apply_keyword_filter(db, Resource, conditions, RESOURCE_SEARCH_COLS, keyword, log_search=False)

    # 列表未使用 content (TEXT) 与 netdisk_links，
    # load_only 避免大字段传输，去掉无用的 selectinload 省一次关联查询
    # 封面/置顶/预览量/标签供后台列表渲染（缩略图、置顶角标、指标列），均为轻量列
    query = (
        select(Resource)
        .options(
            load_only(
                Resource.id, Resource.name, Resource.description, Resource.resource_type,
                Resource.file_size, Resource.download_count, Resource.view_count,
                Resource.is_featured, Resource.is_pinned, Resource.is_published, Resource.cover_image,
                Resource.tags, Resource.created_at,
            )
        )
        .where(*conditions)
        .order_by(Resource.created_at.desc(), Resource.id.desc())
    )
    # PERF-012: 直接 count，无 subquery
    query = query.offset((page - 1) * page_size).limit(page_size)
    resources = (await db.execute(query)).scalars().all()
    items = [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "resource_type": r.resource_type,
            "file_size": r.file_size,
            "download_count": r.download_count,
            "view_count": r.view_count,
            "is_featured": r.is_featured,
            "is_published": r.is_published,
            "is_pinned": r.is_pinned,
            "cover_image": r.cover_image,
            "tags": r.tags,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in resources
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_resource(db: AsyncSession, resource_id: int) -> dict:
    """资源详情（含网盘链接）"""
    result = await db.execute(
        select(Resource)
        .options(selectinload(Resource.netdisk_links))
        .where(Resource.id == resource_id)
    )
    resource = result.scalar_one_or_none()
    if not resource:
        raise AppException(code=404, message="资源不存在")
    return {
        "id": resource.id,
        "name": resource.name,
        "description": resource.description,
        "content": resource.content,
        "resource_type": resource.resource_type,
        "file_size": resource.file_size,
        "content_list": resource.content_list,
        "usage_notes": resource.usage_notes,
        "is_featured": resource.is_featured,
        "is_published": resource.is_published,
        "is_pinned": resource.is_pinned,
        "cover_image": resource.cover_image,
        # 发布作者：供后台表单回填（NULL = 未填写）
        "author_name": resource.author_name,
        "author_avatar_url": resource.author_avatar_url,
        "tags": resource.tags,
        "seo_title": resource.seo_title,
        "seo_description": resource.seo_description,
        "seo_keywords": resource.seo_keywords,
        "netdisk_links": [
            {"platform": link.platform, "url": link.url, "extract_code": link.extract_code}
            for link in resource.netdisk_links
        ],
    }


async def create_resource(db: AsyncSession, data: dict, admin: User) -> dict:
    """创建资源

    data 字段：name/description/resource_type/file_size/content/content_list/
    usage_notes/netdisk_links
    """
    resource = Resource(
        name=data["name"],
        description=data["description"],
        content=sanitize_content(data.get("content")),
        resource_type=data["resource_type"],
        # 大小为可选字段，未填写时存空字符串（数据库列 NOT NULL）
        file_size=data.get("file_size") or "",
        content_list=data.get("content_list"),
        usage_notes=data.get("usage_notes"),
        is_featured=bool(data.get("is_featured")),
        is_pinned=bool(data.get("is_pinned")),
        cover_image=data.get("cover_image") or None,
        # 发布作者：空串归一为 NULL（与 seo_* 一致），前台据 NULL 决定不渲染作者行
        # 发布作者由发布账号自动带出（2026-10-12：不再让运营手填）
        **author_fields(admin, data.get("author_name"), data.get("author_avatar_url")),
        tags=data.get("tags") or None,
        seo_title=data.get("seo_title") or None,
        seo_description=data.get("seo_description") or None,
        seo_keywords=data.get("seo_keywords") or None,
        # 发布状态：默认 True 以保持「保存即上线」的既有习惯；关掉即为草稿，前台不可见
        is_published=data.get("is_published", True),
    )
    db.add(resource)
    await db.flush()

    for idx, link in enumerate(data.get("netdisk_links") or []):
        db.add(
            ResourceNetdiskLink(
                resource_id=resource.id,
                platform=link["platform"],
                url=link["url"],
                extract_code=link.get("extract_code"),
                sort_order=idx,
            )
        )

    await db.refresh(resource)
    record_admin_action(
        db, admin, "content_create",
        target_type="resource", target_id=resource.id,
        summary=f"新建资源《{resource.name}》",
        detail={"resource_type": getattr(resource, "resource_type", None)},
    )
    # 显式提交：本模块 update/delete 均自行 commit，此前只有 create 依赖
    # get_db 的 teardown 兜底提交（**晚于响应发送**）。而缓存失效必须在提交之后，
    # 否则并发读请求可能立刻用旧数据回填缓存，把这次失效「吃掉」。
    await db.commit()
    await invalidate_resources()
    return {"id": resource.id}


async def update_resource(
    db: AsyncSession, resource_id: int, updates: dict, admin: User
) -> None:
    """更新资源（netdisk_links 非 None 时整体覆盖；写入管理操作审计）"""
    result = await db.execute(
        select(Resource)
        .options(selectinload(Resource.netdisk_links))
        .where(Resource.id == resource_id)
    )
    resource = result.scalar_one_or_none()
    if not resource:
        raise AppException(code=404, message="资源不存在")

    if updates.get("name") is not None:
        resource.name = updates["name"]
    if updates.get("description") is not None:
        resource.description = updates["description"]
    if updates.get("content") is not None:
        resource.content = sanitize_content(updates["content"])
    if updates.get("resource_type") is not None:
        resource.resource_type = updates["resource_type"]
    if updates.get("file_size") is not None:
        resource.file_size = updates["file_size"]
    if updates.get("content_list") is not None:
        resource.content_list = updates["content_list"]
    if updates.get("usage_notes") is not None:
        resource.usage_notes = updates["usage_notes"]
    if updates.get("is_featured") is not None:
        resource.is_featured = bool(updates["is_featured"])
    if updates.get("is_pinned") is not None:
        resource.is_pinned = bool(updates["is_pinned"])
    # 封面图/标签允许清空（传空串/空数组 = 清除）
    if updates.get("cover_image") is not None:
        resource.cover_image = updates["cover_image"] or None
    # 发布作者由发布账号自动带出（2026-10-12）：**已有值不覆盖** —— 作者是「谁发布的」，
    # 不是「谁最后改的」；仅历史内容（作者为空）在首次被编辑时自动归属，免去逐条补录。
    fill_author_if_absent(resource, admin, updates.get("author_name"), updates.get("author_avatar_url"))
    if updates.get("tags") is not None:
        resource.tags = updates["tags"] or None
    # SEO 字段允许清空（传空串 = 清除，前台回落默认标题/简介）
    if updates.get("seo_title") is not None:
        resource.seo_title = updates["seo_title"] or None
    if updates.get("seo_description") is not None:
        resource.seo_description = updates["seo_description"] or None
    if updates.get("seo_keywords") is not None:
        resource.seo_keywords = updates["seo_keywords"] or None
    # 发布状态：True=前台可见，False=草稿（公开列表/详情/首页/sitemap 一律过滤）
    if updates.get("is_published") is not None:
        was_published = resource.is_published
        resource.is_published = updates["is_published"]
        if was_published and not resource.is_published:
            await notify_favoriters_unpublished(
                db, target_type="resource", target_id=resource.id, title=resource.name
            )
    if updates.get("netdisk_links") is not None:
        # 批量删除旧链接（单条 DELETE 语句，替代逐条 delete 的 N 次往返）
        await db.execute(
            delete(ResourceNetdiskLink)
            .where(ResourceNetdiskLink.resource_id == resource_id)
            .execution_options(synchronize_session=False),
        )
        for idx, link in enumerate(updates["netdisk_links"]):
            db.add(
                ResourceNetdiskLink(
                    resource_id=resource.id,
                    platform=link["platform"],
                    url=link["url"],
                    extract_code=link.get("extract_code"),
                    sort_order=idx,
                )
            )
    record_admin_action(
        db, admin, "content_update",
        target_type="resource", target_id=resource_id,
        summary=f"编辑资源《{resource.name}》",
        detail={"fields": sorted(updates.keys()), "changes": audit_changes(resource, updates)},
    )
    await db.commit()
    await invalidate_resources()


async def delete_resource(db: AsyncSession, resource_id: int, admin: User) -> None:
    """删除资源 —— **软删除**（进回收站，可恢复）。

    与改造前的差异：
    - 不再物理删除资源行与网盘链接（恢复时内容才完整），只打 `deleted_at` 并强制下架；
    - 收藏 / 评论仍按原语义清理（用户列表里不留点开即 404 的死条目）。
    """
    resource = (
        await db.execute(
            select(Resource).where(
                Resource.id == resource_id,
                Resource.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not resource:
        raise AppException(code=404, message="资源不存在")
    # 收藏/评论都只存快照、无外键（P1-7/P1-9）：清哪几张表由注册表的能力标记决定
    await purge_content_records(db, "resource", [resource_id])
    mark_deleted(resource)
    record_admin_action(
        db, admin, "content_delete",
        target_type="resource", target_id=resource_id,
        summary=f"删除资源《{resource.name}》（已移入回收站，可恢复）",
    )
    await db.commit()
    await invalidate_resources()


async def batch_delete_resources(db: AsyncSession, resource_ids: list[int], admin: User) -> dict:
    """批量删除资源（**软删除**，进回收站；语义与单条 delete_resource 一致）

    部分 id 已不存在（或已在回收站）时不报错，按实际处理的条数返回。
    """
    resources = (
        await db.execute(
            select(Resource).where(
                Resource.id.in_(resource_ids),
                Resource.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not resources:
        raise AppException(code=404, message="资源不存在")
    ids = [r.id for r in resources]

    # 收藏与评论按批清理（P1-7/P1-9，同单条删除）
    await purge_content_records(db, "resource", ids)
    for r in resources:
        mark_deleted(r)

    record_admin_action(
        db, admin, "content_delete",
        target_type="resource",
        summary=f"批量删除 {len(ids)} 个资源（已移入回收站，可恢复）",
        detail={"count": len(ids), "ids": summarize_ids(ids)},
    )
    await db.commit()
    await invalidate_resources()
    return {"deleted": len(ids)}


async def restore_resource(
    db: AsyncSession, resource_id: int, admin: User, *, republish: bool = False
) -> dict:
    """从回收站恢复资源（默认恢复为草稿，``republish=True`` 时同时重新发布）"""
    resource = (
        await db.execute(
            select(Resource).where(
                Resource.id == resource_id,
                Resource.deleted_at.isnot(None),
            )
        )
    ).scalar_one_or_none()
    if not resource:
        raise AppException(code=404, message="回收站中没有该资源")

    mark_restored(resource, republish=republish)
    record_admin_action(
        db, admin, "content_restore",
        target_type="resource", target_id=resource_id,
        summary=f"恢复资源《{resource.name}》" + ("（并重新发布）" if republish else "（保持草稿）"),
    )
    await db.commit()
    await invalidate_resources()
    return {"restored": 1, "republished": republish}


async def batch_restore_resources(
    db: AsyncSession, resource_ids: list[int], admin: User, *, republish: bool = False
) -> dict:
    """批量从回收站恢复；返回实际恢复条数"""
    resources = (
        await db.execute(
            select(Resource).where(
                Resource.id.in_(resource_ids),
                Resource.deleted_at.isnot(None),
            )
        )
    ).scalars().all()
    if not resources:
        raise AppException(code=404, message="回收站中没有这些资源")
    ids = [r.id for r in resources]
    for r in resources:
        mark_restored(r, republish=republish)

    record_admin_action(
        db, admin, "content_restore",
        target_type="resource",
        summary=f"批量恢复 {len(ids)} 个资源" + ("（并重新发布）" if republish else "（保持草稿）"),
        detail={"count": len(ids), "ids": summarize_ids(ids)},
    )
    await db.commit()
    await invalidate_resources()
    return {"restored": len(ids), "republished": republish}


