"""管理后台 - 扩展 CRUD 服务

从 admin.py 抽取的 ORM 操作。保持与原 admin 路由完全相同的响应格式。
"""

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only

from app.models.node import Plugin, PluginSource
from app.services.content_notify import notify_favoriters_unpublished
from app.services.content_purge import purge_content_records
from app.models.user import User
from app.core.exceptions import AppException
from app.services.admin_audit_service import audit_changes, record_admin_action, summarize_ids
from app.services.content_author import author_fields, fill_author_if_absent
from app.utils.sanitize import sanitize_content
from app.services.content_cache import invalidate_plugins
from app.services.search import PLUGIN_SEARCH_COLS, apply_keyword_filter


async def list_plugins(
    db: AsyncSession, page: int, page_size: int, keyword: str = None, category: str = None
) -> dict:
    """扩展列表（含搜索 / 分类筛选 / 分页）"""
    conditions = []
    if category and category != "all":
        conditions.append(Plugin.category == category)

    # 搜索与公开端共用同一构造器（第三轮增量审查 P1-41）：
    # 此前只搜 name，而公开端搜 name/description/content —— 行为漂移已是事实
    total = await apply_keyword_filter(db, Plugin, conditions, PLUGIN_SEARCH_COLS, keyword, log_search=False)
    # 列表未使用 content (MEDIUMTEXT)，load_only 避免大字段传输
    query = (
        select(Plugin)
        .options(
            load_only(
                Plugin.id, Plugin.name, Plugin.category, Plugin.view_count,
                Plugin.description, Plugin.created_at, Plugin.updated_at,
            )
        )
        .where(*conditions)
        .order_by(Plugin.created_at.desc(), Plugin.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    plugins = (await db.execute(query)).scalars().all()

    items = [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "view_count": p.view_count,
            "description": p.description,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in plugins
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_plugin(db: AsyncSession, plugin_id: int) -> dict:
    """扩展详情（含来源）"""
    result = await db.execute(
        select(Plugin)
        .options(selectinload(Plugin.sources))
        .where(Plugin.id == plugin_id)
    )
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise AppException(code=404, message="扩展不存在")
    return {
        "id": plugin.id,
        "name": plugin.name,
        "category": plugin.category,
        "view_count": plugin.view_count,
        "description": plugin.description,
        "content": plugin.content,
        # 发布作者：供后台表单回填（NULL = 未填写）
        "author_name": plugin.author_name,
        "author_avatar_url": plugin.author_avatar_url,
        # SEO 信息（台账 P1-17）：与资源/教程同名字段，供后台表单回填
        "seo_title": plugin.seo_title,
        "seo_description": plugin.seo_description,
        "seo_keywords": plugin.seo_keywords,
        "sources": [
            {"id": s.id, "source_name": s.source_name, "url": s.url, "extract_code": s.extract_code}
            for s in plugin.sources
        ],
    }


async def create_plugin(db: AsyncSession, data: dict, admin: User) -> dict:
    """创建扩展

    data 字段：name/category/description/content/sources/
    author_name/author_avatar_url/seo_title/seo_description/seo_keywords
    （2026-10-12：platform/version/rating/install_count 已随迁移 0040 删列，不再接收）

    写入管理操作审计（第二轮增量审查 P2：此前 create/update 无审计）。
    """
    plugin = Plugin(
        name=data["name"],
        category=data["category"],
        description=data["description"],
        content=sanitize_content(data.get("content")),
        # 发布作者：空串归一为 NULL（与 seo_* 一致），前台据 NULL 决定不渲染作者行
        # 发布作者由发布账号自动带出（2026-10-12：不再让运营手填）
        **author_fields(admin, data.get("author_name"), data.get("author_avatar_url")),
        # 空串归一为 NULL：与资源/教程一致，NULL = 未填写（前台回落默认标题）
        seo_title=data.get("seo_title") or None,
        seo_description=data.get("seo_description") or None,
        seo_keywords=data.get("seo_keywords") or None,
        # 发布状态：默认 True 以保持「保存即上线」的既有习惯；关掉即为草稿，前台不可见
        is_published=data.get("is_published", True),
    )
    db.add(plugin)
    await db.flush()

    for link in data.get("sources") or []:
        db.add(
            PluginSource(
                plugin_id=plugin.id,
                source_name=link["source_name"],
                url=link["url"],
                extract_code=link.get("extract_code"),
            )
        )

    await db.refresh(plugin)
    record_admin_action(
        db, admin, "content_create",
        target_type="plugin", target_id=plugin.id,
        summary=f"新建扩展《{plugin.name}》",
        detail={"category": plugin.category},
    )
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    await invalidate_plugins()
    return {"id": plugin.id}


async def update_plugin(
    db: AsyncSession, plugin_id: int, updates: dict, admin: User
) -> None:
    """更新扩展（sources 非 None 时整体覆盖；写入管理操作审计）"""
    result = await db.execute(
        select(Plugin)
        .options(selectinload(Plugin.sources))
        .where(Plugin.id == plugin_id)
    )
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise AppException(code=404, message="扩展不存在")

    if updates.get("name") is not None:
        plugin.name = updates["name"]
    if updates.get("category") is not None:
        plugin.category = updates["category"]
    if updates.get("description") is not None:
        plugin.description = updates["description"]
    if updates.get("content") is not None:
        plugin.content = sanitize_content(updates["content"])
    # 发布作者由发布账号自动带出（2026-10-12）：**已有值不覆盖** —— 作者是「谁发布的」，
    # 不是「谁最后改的」；仅历史内容（作者为空）在首次被编辑时自动归属，免去逐条补录。
    fill_author_if_absent(plugin, admin, updates.get("author_name"), updates.get("author_avatar_url"))
    # SEO 信息（台账 P1-17）：传空串 = 清除（回落默认标题），与资源/教程同语义
    if updates.get("seo_title") is not None:
        plugin.seo_title = updates["seo_title"] or None
    if updates.get("seo_description") is not None:
        plugin.seo_description = updates["seo_description"] or None
    if updates.get("seo_keywords") is not None:
        plugin.seo_keywords = updates["seo_keywords"] or None
    # 发布状态：True=前台可见，False=草稿（公开列表/详情/首页/sitemap 一律过滤）
    if updates.get("is_published") is not None:
        was_published = plugin.is_published
        plugin.is_published = updates["is_published"]
        if was_published and not plugin.is_published:
            await notify_favoriters_unpublished(
                db, target_type="plugin", target_id=plugin.id, title=plugin.name
            )
    if updates.get("sources") is not None:
        # 批量删除旧来源（单条 DELETE 语句，替代逐条 delete 的 N 次往返）
        await db.execute(
            delete(PluginSource)
            .where(PluginSource.plugin_id == plugin_id)
            .execution_options(synchronize_session=False),
        )
        for link in updates["sources"]:
            db.add(
                PluginSource(
                    plugin_id=plugin.id,
                    source_name=link["source_name"],
                    url=link["url"],
                    extract_code=link.get("extract_code"),
                )
            )
    record_admin_action(
        db, admin, "content_update",
        target_type="plugin", target_id=plugin_id,
        summary=f"编辑扩展《{plugin.name}》",
        detail={"fields": sorted(updates.keys()), "changes": audit_changes(plugin, updates)},
    )
    await db.commit()
    await invalidate_plugins()


async def delete_plugin(db: AsyncSession, plugin_id: int, admin: User) -> None:
    """删除扩展（硬删除，依赖级联删除 sources）

    硬删除不可恢复，写入管理操作审计（台账 P2-9），审计行与删除同事务。
    """
    plugin = (
        await db.execute(select(Plugin).where(Plugin.id == plugin_id))
    ).scalar_one_or_none()
    if not plugin:
        raise AppException(code=404, message="扩展不存在")
    # 收藏只存快照、无外键（P1-7）：不同步清理会在用户「我的收藏」留下死条目。
    # 清哪几张表由 core.content_types 的能力标记决定（EXT-07）
    await purge_content_records(db, "plugin", [plugin_id])
    await db.delete(plugin)
    record_admin_action(
        db, admin, "content_delete",
        target_type="plugin", target_id=plugin_id,
        summary=f"删除扩展《{plugin.name}》",
    )
    await db.commit()
    await invalidate_plugins()


async def batch_delete_plugins(db: AsyncSession, plugin_ids: list[int], admin: User) -> dict:
    """批量删除扩展（硬删除，来源链接一并清除）

    部分 id 已不存在时不报错，按实际存在的删除并返回数量。
    """
    # 先清理来源链接（外键无 ON DELETE CASCADE 时需显式删除；单条 DELETE 按批合并）
    await db.execute(
        delete(PluginSource)
        .where(PluginSource.plugin_id.in_(plugin_ids))
        .execution_options(synchronize_session=False),
    )
    # 收藏按批清理（P1-7，同单条删除）
    await purge_content_records(db, "plugin", plugin_ids)
    result = await db.execute(
        delete(Plugin)
        .where(Plugin.id.in_(plugin_ids))
        .execution_options(synchronize_session=False),
    )
    deleted = result.rowcount or 0
    # 先判空再记审计：一个都没删掉时抛 404，此时**不能**留下审计行
    if not deleted:
        raise AppException(code=404, message="扩展不存在")
    record_admin_action(
        db, admin, "content_delete",
        target_type="plugin",
        summary=f"批量删除 {deleted} 个扩展",
        detail={"count": deleted, "ids": summarize_ids(plugin_ids)},
    )
    await db.commit()
    await invalidate_plugins()
    return {"deleted": deleted}
