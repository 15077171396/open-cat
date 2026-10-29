"""教程章节（tutorial_nodes）服务

树形章节的读写。PandaWiki 文档节点模式：
- 前台左树 = 章节间层级（本表），右大纲 = 单章正文内标题（前端提取），两级数据源分离
- 管理后台按章编辑：选中一个章节只编辑该章 content，替代原先整篇大字段编辑
"""

import json
import random

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.tutorial import Tutorial
from app.services.view_counter import register_view
from app.models.tutorial_node import TutorialNode
from app.core.exceptions import AppException, NotFoundError
from app.utils.sanitize import sanitize_content
from app.models.user import User
from app.services.admin_audit_service import audit_changes, record_admin_action
from app.redis import log_redis_degraded, redis_client


async def _get_tutorial_or_404(db: AsyncSession, tutorial_id: int, *, published_only: bool = False) -> None:
    """教程存在性/发布状态探测。

    仅投影 id 列（列查询），避免为一次存在性校验加载 content/introduction
    两个 MEDIUMTEXT 大字段（单章最大 16MB，公开免登录接口高频触发）。
    """
    query = select(Tutorial.id).where(Tutorial.id == tutorial_id)
    if published_only:
        query = query.where(Tutorial.is_published == True)  # noqa: E712
    if (await db.execute(query)).scalar_one_or_none() is None:
        raise NotFoundError("教程不存在")


async def _load_nodes(db: AsyncSession, tutorial_id: int, *, with_content: bool = False) -> list[TutorialNode]:
    """按 (parent_id, sort, id) 顺序加载某教程全部章节（树重建在内存中完成）。

    with_content=False（默认，公开树/翻章导航等仅需小字段场景）时用 load_only
    裁剪掉 content 大字段，避免整章正文无谓传输；True 时（管理后台编辑）全量加载。
    """
    query = (
        select(TutorialNode)
        .where(TutorialNode.tutorial_id == tutorial_id)
        .order_by(TutorialNode.parent_id.asc(), TutorialNode.sort.asc(), TutorialNode.id.asc())
    )
    if not with_content:
        query = query.options(
            load_only(TutorialNode.id, TutorialNode.title, TutorialNode.sort, TutorialNode.parent_id)
        )
    result = await db.execute(query)
    return list(result.scalars().all())


def _build_tree(nodes: list[TutorialNode], *, with_content: bool) -> list[dict]:
    """把扁平章节列表组装为嵌套树。

    with_content=False 时仅含导航小字段（公开树接口/介绍页目录）；
    True 时携带正文（管理后台编辑）。
    """
    by_parent: dict[int | None, list[TutorialNode]] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)

    def serialize(n: TutorialNode) -> dict:
        children = [serialize(c) for c in by_parent.get(n.id, [])]
        item: dict = {"id": n.id, "title": n.title, "sort": n.sort}
        if with_content:
            item["content"] = n.content
            item["parent_id"] = n.parent_id
        if children:
            item["children"] = children
        return item

    return [serialize(n) for n in by_parent.get(None, [])]


def _flatten_tree(tree: list[dict]) -> list[dict]:
    """树 → 前序扁平列表（含 depth），用于「上一章/下一章」线性导航"""
    flat: list[dict] = []

    def walk(nodes: list[dict], depth: int) -> None:
        for n in nodes:
            flat.append({"id": n["id"], "title": n["title"], "depth": depth})
            walk(n.get("children", []), depth + 1)

    walk(tree, 0)
    return flat


# 章节导航（上一章/下一章）的 id 序列缓存（第五轮审查 PERF-05）。
#
# 为什么需要：`get_node_detail` 只为算 prev/next，就要把该教程**全部章节**读出来、
# 建树、再扁平化 —— 500 章的教程每翻一页都是一次 O(N) 查询 + O(N) 内存分配。
# 而「章节顺序」在一次编辑会话里是稳定的，完全可以缓存。
#
# ⚠️ 顺序是**树前序**（父 → 它的子章节 → 下一个父章节），不是 `sort_order`：
# 缓存里存的就是扁平化后的 id 序列，不要改成按 sort 排序 —— 那会改变「下一章」指向哪一章。
#
# 失效：任何章节增删改/移动都会 `invalidate_nav_cache`，所以正常情况下不会读到旧序列。
_NAV_CACHE_TTL = 300
_NAV_CACHE_JITTER = 60


def _nav_cache_key(tutorial_id: int) -> str:
    return f"tutorial_nav:{tutorial_id}"


async def invalidate_nav_cache(tutorial_id: int) -> None:
    """章节结构变化后清掉导航序列缓存（Redis 不可用时静默跳过 —— 最多旧 5 分钟）"""
    try:
        await redis_client.delete(_nav_cache_key(tutorial_id))
    except Exception as exc:  # noqa: BLE001 缓存失效失败不能阻断写操作
        log_redis_degraded("tutorial_node_service.invalidate_nav_cache", exc)


async def _nav_ids(db: AsyncSession, tutorial_id: int) -> list[int]:
    """该教程的章节 id 前序序列（带 Redis 缓存，未命中回落到查库）"""
    key = _nav_cache_key(tutorial_id)
    try:
        raw = await redis_client.get(key)
    except Exception as exc:  # noqa: BLE001 缓存读失败直接查库
        log_redis_degraded("tutorial_node_service._nav_ids", exc)
        raw = None
    if raw:
        try:
            ids = json.loads(raw)
            if isinstance(ids, list):
                return ids
        except (ValueError, TypeError):
            pass  # 结构损坏：当作未命中，下面重建

    nodes = await _load_nodes(db, tutorial_id)
    flat = _flatten_tree(_build_tree(nodes, with_content=False))
    ids = [n["id"] for n in flat]
    try:
        await redis_client.set(
            key, json.dumps(ids), ex=_NAV_CACHE_TTL + random.randint(0, _NAV_CACHE_JITTER)
        )
    except Exception as exc:  # noqa: BLE001 缓存写失败不影响本次返回
        log_redis_degraded("tutorial_node_service._nav_ids_set", exc)
    return ids


# 公开端（前台阅读）

async def get_node_tree(db: AsyncSession, tutorial_id: int) -> dict:
    """章节树（公开，仅导航小字段；供介绍页目录与阅读页左树使用）"""
    await _get_tutorial_or_404(db, tutorial_id, published_only=True)
    nodes = await _load_nodes(db, tutorial_id)
    tree = _build_tree(nodes, with_content=False)
    return {"nodes": tree, "flat": _flatten_tree(tree)}


async def get_node_detail(db: AsyncSession, tutorial_id: int, node_id: int, *, count_view: bool = False) -> dict:
    """单章正文（前台阅读页，登录与计时门槛由路由层把关）"""
    await _get_tutorial_or_404(db, tutorial_id, published_only=True)
    node = (
        await db.execute(
            select(TutorialNode).where(
                TutorialNode.id == node_id,
                TutorialNode.tutorial_id == tutorial_id,
            )
        )
    ).scalar_one_or_none()
    if not node:
        raise NotFoundError("章节不存在")

    # 线性导航：上一章/下一章（树前序）。id 序列走缓存，不再每次重建整棵树
    nav_ids = await _nav_ids(db, tutorial_id)
    index = next((i for i, nid in enumerate(nav_ids) if nid == node_id), -1)
    prev_id = nav_ids[index - 1] if index > 0 else None
    next_id = nav_ids[index + 1] if 0 <= index < len(nav_ids) - 1 else None

    # 浏览计数：仅前端显式传 count_view（阅读页会话内首次加载）时自增，
    # 章节间翻页不重复计数。走 Redis 累加（详见 view_counter），不再每次写库
    if count_view:
        await register_view(db, "tutorial", tutorial_id)

    return {
        "id": node.id,
        "title": node.title,
        "content": node.content,
        "prev_node_id": prev_id,
        "next_node_id": next_id,
    }


# 管理端（后台章节编辑）

async def admin_get_tree(db: AsyncSession, tutorial_id: int) -> dict:
    """章节树（含正文，管理后台编辑用）"""
    await _get_tutorial_or_404(db, tutorial_id)
    nodes = await _load_nodes(db, tutorial_id, with_content=True)
    return {"nodes": _build_tree(nodes, with_content=True)}


async def _get_node_or_404(db: AsyncSession, tutorial_id: int, node_id: int) -> TutorialNode:
    node = (
        await db.execute(
            select(TutorialNode).where(
                TutorialNode.id == node_id,
                TutorialNode.tutorial_id == tutorial_id,
            )
        )
    ).scalar_one_or_none()
    if not node:
        raise AppException(code=404, message="章节不存在")
    return node


async def _validate_parent(db: AsyncSession, tutorial_id: int, parent_id: int | None) -> None:
    """父章节必须存在且属于同一教程"""
    if parent_id is None:
        return
    parent = (
        await db.execute(
            select(TutorialNode).where(
                TutorialNode.id == parent_id,
                TutorialNode.tutorial_id == tutorial_id,
            )
        )
    ).scalar_one_or_none()
    if not parent:
        raise AppException(code=400, message="父章节不存在或不属于该教程")


async def admin_create_node(
    db: AsyncSession, tutorial_id: int, data: dict, admin: User
) -> dict:
    """新建章节（追加到指定父级的同级末尾）"""
    await _get_tutorial_or_404(db, tutorial_id)
    parent_id = data.get("parent_id")
    await _validate_parent(db, tutorial_id, parent_id)

    # 同级末尾追加：取当前同级最大 sort + 1
    max_sort = (
        await db.execute(
            select(func.max(TutorialNode.sort)).where(
                TutorialNode.tutorial_id == tutorial_id,
                TutorialNode.parent_id == parent_id,
            )
        )
    ).scalar() or 0

    node = TutorialNode(
        tutorial_id=tutorial_id,
        parent_id=parent_id,
        sort=max_sort + 1,
        title=data["title"],
        content=sanitize_content(data.get("content") or ""),
    )
    db.add(node)
    await db.flush()  # 先拿到主键，审计才能归因到具体章节
    record_admin_action(
        db, admin, "content_create",
        target_type="tutorial_node", target_id=node.id,
        summary=f"新建章节《{node.title}》(教程 {tutorial_id})",
        detail={"tutorial_id": tutorial_id, "parent_id": parent_id},
    )
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    await invalidate_nav_cache(tutorial_id)
    return {"id": node.id}


async def admin_update_node(
    db: AsyncSession, tutorial_id: int, node_id: int, updates: dict, admin: User
) -> None:
    """更新章节标题/正文"""
    node = await _get_node_or_404(db, tutorial_id, node_id)
    if updates.get("title") is not None:
        node.title = updates["title"]
    if updates.get("content") is not None:
        node.content = sanitize_content(updates["content"])
    record_admin_action(
        db, admin, "content_update",
        target_type="tutorial_node", target_id=node_id,
        summary=f"编辑章节《{node.title}》(教程 {tutorial_id})",
        detail={"fields": sorted(updates.keys()), "changes": audit_changes(node, updates)},
    )
    await db.commit()
    await invalidate_nav_cache(tutorial_id)


async def admin_delete_node(
    db: AsyncSession, tutorial_id: int, node_id: int, admin: User
) -> dict:
    """删除章节（递归删除其子孙；收集被删 id 供前端清理本地树状态）"""
    node = await _get_node_or_404(db, tutorial_id, node_id)

    # 收集子孙 id（递归 CTE 简化版：层级一般很浅，逐层查询足够），并按层记录
    # 过滤条件带 tutorial_id：防止历史脏数据出现跨教程 parent 指向时误删其他教程的章节
    levels: list[list[int]] = [[node.id]]
    frontier = [node.id]
    while frontier:
        children = (
            await db.execute(
                select(TutorialNode.id).where(
                    TutorialNode.tutorial_id == tutorial_id,
                    TutorialNode.parent_id.in_(frontier),
                )
            )
        ).scalars().all()
        if children:
            levels.append(list(children))
        frontier = list(children)

    # 自最深层向上逐层删除：parent_id 外键无 ON DELETE CASCADE，
    # 单条 IN 批删时父行先于子行被删会触发 FK 约束 1451，分层删则子行必先于父行消失
    for level in reversed(levels):
        await db.execute(
            delete(TutorialNode).where(TutorialNode.id.in_(level)).execution_options(synchronize_session=False)
        )
    record_admin_action(
        db, admin, "content_delete",
        target_type="tutorial_node", target_id=node_id,
        summary=f"删除章节《{node.title}》及其 {(len(levels) - 1)} 层子章节 (教程 {tutorial_id})",
        detail={"tutorial_id": tutorial_id, "deleted_count": sum(len(lv) for lv in levels)},
    )
    await db.commit()
    await invalidate_nav_cache(tutorial_id)

    ids = [i for level in levels for i in level]
    return {"deleted_ids": ids}


async def admin_move_node(db: AsyncSession, tutorial_id: int, node_id: int, direction: str) -> dict:
    """同级内移动章节（up=与上一章互换，down=与下一章互换）"""
    node = await _get_node_or_404(db, tutorial_id, node_id)
    # 注意：await db.execute(...) 的结果必须先落变量再 .scalars()。
    # 若写成 (await db.execute(...)).scalars().all() 但分组括号未在链式调用前闭合，
    # 会按优先级解析为 await (db.execute(...).scalars().all())，对 coroutine 调 .scalars() 报 AttributeError。
    result = await db.execute(
        select(TutorialNode)
        .where(
            TutorialNode.tutorial_id == tutorial_id,
            TutorialNode.parent_id == node.parent_id,
        )
        .order_by(TutorialNode.sort.asc(), TutorialNode.id.asc())
    )
    siblings = list(result.scalars().all())
    index = next((i for i, n in enumerate(siblings) if n.id == node_id), -1)
    if index < 0:
        raise AppException(code=400, message="章节不存在")

    if direction == "up" and index > 0:
        siblings[index - 1], siblings[index] = siblings[index], siblings[index - 1]
    elif direction == "down" and index < len(siblings) - 1:
        siblings[index + 1], siblings[index] = siblings[index], siblings[index + 1]
    else:
        return {"moved": False}

    # 位置未变化（已到边界）时直接返回
    for new_sort, n in enumerate(siblings, start=1):
        n.sort = new_sort
    await db.commit()
    await invalidate_nav_cache(tutorial_id)
    return {"moved": True}
