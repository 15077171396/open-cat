"""管理后台 - 知识百科节点 CRUD 服务

从 admin.py 抽取的 ORM 操作。保持与原 admin 路由完全相同的响应格式。
"""

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.node import Node
from app.models.user import User
from app.core.exceptions import AppException
from app.services.admin_audit_service import audit_changes, record_admin_action, summarize_ids
from app.services.content_author import author_fields, fill_author_if_absent
from app.utils.sanitize import sanitize_content
from app.services.content_notify import notify_favoriters_unpublished
from app.services.content_purge import purge_content_records
from app.services.content_cache import invalidate_nodes
from app.services.search import NODE_SEARCH_COLS, apply_keyword_filter


async def list_nodes(
    db: AsyncSession, page: int, page_size: int, keyword: str = None, category: str = None
) -> dict:
    """节点列表（含搜索 / 分类筛选 / 分页）"""
    conditions = []
    if category and category != "all":
        conditions.append(Node.category == category)

    # 搜索与公开端共用同一构造器（全文优先 + LIKE 兜底 over name/description/content）。
    # 此前只搜 name，导致「前台搜得到、后台搜不到」（第三轮增量审查 P1-41）。
    total = await apply_keyword_filter(db, Node, conditions, NODE_SEARCH_COLS, keyword, log_search=False)

    # 列表仅用小字段，load_only 避免 MEDIUMTEXT content 与 JSON 大字段传输
    # 封面/预览量/更新时间供后台列表渲染（缩略图、指标列、最近更新），均为轻量列
    query = (
        select(Node)
        .options(
            load_only(
                Node.id, Node.name, Node.category, Node.description,
                Node.cover_image, Node.view_count, Node.updated_at,
            )
        )
        .where(*conditions)
        .order_by(Node.created_at.desc(), Node.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    nodes = (await db.execute(query)).scalars().all()
    items = [
        {
            "id": n.id,
            "name": n.name,
            "category": n.category,
            # 截断到 80 字：列表单元只显示 2 行，长简介不必整段传输
            "description": (n.description or "")[:80],
            "cover_image": n.cover_image,
            "view_count": n.view_count,
            "updated_at": n.updated_at.isoformat() if n.updated_at else None,
        }
        for n in nodes
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_node(db: AsyncSession, node_id: int) -> dict:
    """节点详情"""
    node = (
        await db.execute(select(Node).where(Node.id == node_id))
    ).scalar_one_or_none()
    if not node:
        raise AppException(code=404, message="节点不存在")
    return {
        "id": node.id,
        "name": node.name,
        "category": node.category,
        "description": node.description,
        "content": node.content,
        "inputs": node.inputs,
        "outputs": node.outputs,
        "params": node.params,
        "examples": node.examples,
        # 封面 + 发布作者（2026-10-12）：供后台表单回填（NULL = 未填写）
        "cover_image": node.cover_image,
        # 预览量：后台编辑页「发布检查」脚注展示
        "view_count": node.view_count,
        "author_name": node.author_name,
        "author_avatar_url": node.author_avatar_url,
        # SEO 信息（台账 P1-17）：与资源/教程同名字段，供后台表单回填
        "is_published": node.is_published,
        "seo_title": node.seo_title,
        "seo_description": node.seo_description,
        "seo_keywords": node.seo_keywords,
    }


async def create_node(db: AsyncSession, data: dict, admin: User) -> dict:
    """创建节点

    data 字段：name/category/description/content/inputs/outputs/params/examples/
    seo_title/seo_description/seo_keywords

    写入管理操作审计（第二轮增量审查 P2：此前 create/update **无审计**，
    内容被改动时只能靠 `updated_at` 猜「谁改的」）。
    """
    node = Node(
        name=data["name"],
        category=data["category"],
        description=data["description"],
        content=sanitize_content(data.get("content")),
        inputs=data.get("inputs"),
        outputs=data.get("outputs"),
        params=data.get("params"),
        examples=data.get("examples"),
        # 封面 + 发布作者：空串归一为 NULL（前台据 NULL 决定不渲染对应区域）
        cover_image=data.get("cover_image") or None,
        # 发布作者由发布账号自动带出（2026-10-12：不再让运营手填）
        **author_fields(admin, data.get("author_name"), data.get("author_avatar_url")),
        # 空串归一为 NULL：与资源/教程一致，NULL = 未填写（前台回落默认标题）
        seo_title=data.get("seo_title") or None,
        seo_description=data.get("seo_description") or None,
        seo_keywords=data.get("seo_keywords") or None,
        # 发布状态：默认 True 以保持「保存即上线」的既有习惯；关掉即为草稿，前台不可见
        is_published=data.get("is_published", True),
    )
    db.add(node)
    # 先 flush 拿到 generate_id 生成的主键，审计才能归因到具体节点
    await db.flush()
    record_admin_action(
        db, admin, "content_create",
        target_type="node", target_id=node.id,
        summary=f"新建节点《{node.name}》",
        detail={"category": node.category},
    )
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    await invalidate_nodes()
    return {"id": node.id}


async def update_node(db: AsyncSession, node_id: int, updates: dict, admin: User) -> None:
    """更新节点（写入管理操作审计）"""
    node = (
        await db.execute(select(Node).where(Node.id == node_id))
    ).scalar_one_or_none()
    if not node:
        raise AppException(code=404, message="节点不存在")

    if updates.get("name") is not None:
        node.name = updates["name"]
    if updates.get("category") is not None:
        node.category = updates["category"]
    if updates.get("description") is not None:
        node.description = updates["description"]
    if updates.get("content") is not None:
        node.content = sanitize_content(updates["content"])
    if updates.get("inputs") is not None:
        node.inputs = updates["inputs"]
    if updates.get("outputs") is not None:
        node.outputs = updates["outputs"]
    if updates.get("params") is not None:
        node.params = updates["params"]
    if updates.get("examples") is not None:
        node.examples = updates["examples"]
    # 封面图：传空串 = 清除（回退分类图标底色）
    if updates.get("cover_image") is not None:
        node.cover_image = updates["cover_image"] or None
    # 发布作者由发布账号自动带出（2026-10-12）：**已有值不覆盖** —— 作者是「谁发布的」，
    # 不是「谁最后改的」；仅历史内容（作者为空）在首次被编辑时自动归属，免去逐条补录。
    fill_author_if_absent(node, admin, updates.get("author_name"), updates.get("author_avatar_url"))
    # SEO 信息（台账 P1-17）：传空串 = 清除（回落默认标题），与资源/教程同语义
    if updates.get("seo_title") is not None:
        node.seo_title = updates["seo_title"] or None
    if updates.get("seo_description") is not None:
        node.seo_description = updates["seo_description"] or None
    if updates.get("seo_keywords") is not None:
        node.seo_keywords = updates["seo_keywords"] or None
    # 发布状态：True=前台可见，False=草稿（公开列表/详情/首页/sitemap 一律过滤）
    if updates.get("is_published") is not None:
        was_published = node.is_published
        node.is_published = updates["is_published"]
        # 已发布 → 下架：给收藏过它的用户发一条站内消息（同事务，回滚则不发）
        if was_published and not node.is_published:
            await notify_favoriters_unpublished(
                db, target_type="node", target_id=node.id, title=node.name
            )
    record_admin_action(
        db, admin, "content_update",
        target_type="node", target_id=node_id,
        summary=f"编辑节点《{node.name}》",
        detail={"fields": sorted(updates.keys()), "changes": audit_changes(node, updates)},
    )
    await db.commit()
    await invalidate_nodes()


async def delete_node(db: AsyncSession, node_id: int, admin: User) -> None:
    """删除节点（硬删除）

    硬删除不可恢复，写入管理操作审计（台账 P2-9），审计行与删除同事务。
    """
    node = (
        await db.execute(select(Node).where(Node.id == node_id))
    ).scalar_one_or_none()
    if not node:
        raise AppException(code=404, message="节点不存在")
    # 收藏/评论都只存快照、无外键，不会级联（P1-7/P1-9）：不同步清理会在
    # 用户「我的收藏」留下死条目、并留下指向不存在节点的孤儿评论。
    # 清哪几张表由 core.content_types 的能力标记决定（EXT-07），不再手工铺调用
    await purge_content_records(db, "node", [node_id])
    await db.delete(node)
    record_admin_action(
        db, admin, "content_delete",
        target_type="node", target_id=node_id,
        summary=f"删除节点《{node.name}》",
    )
    await db.commit()
    await invalidate_nodes()


async def batch_delete_nodes(db: AsyncSession, node_ids: list[int], admin: User) -> dict:
    """批量删除节点（硬删除，单条 DELETE 按批合并）

    部分 id 已不存在时不报错，按实际存在的删除并返回数量。
    """
    # 收藏与评论按批清理（P1-7/P1-9）：条件里必须同时含 target_type，
    # 否则会把同 id 的其它类型内容评论一起删掉 —— 由 content_purge 统一保证
    await purge_content_records(db, "node", node_ids)
    result = await db.execute(
        delete(Node)
        .where(Node.id.in_(node_ids))
        .execution_options(synchronize_session=False),
    )
    deleted = result.rowcount or 0
    # 先判空再记审计：一个都没删掉时抛 404，此时**不能**留下审计行
    if not deleted:
        raise AppException(code=404, message="节点不存在")
    record_admin_action(
        db, admin, "content_delete",
        target_type="node",
        summary=f"批量删除 {deleted} 个节点",
        detail={"count": deleted, "ids": summarize_ids(node_ids)},
    )
    await db.commit()
    await invalidate_nodes()
    return {"deleted": deleted}
