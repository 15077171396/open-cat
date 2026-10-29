"""节点百科服务（公开端）

从 api/nodes.py 抽取的 ORM 操作。保持与原路由完全相同的响应格式。
"""

import json
import random

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.node import Node
from app.models.category import Category
from app.services.content_author import (
    apply_live_author_one,
    apply_live_authors,
)
from app.core.exceptions import NotFoundError
from app.core.content_types import CONTENT_TYPES
from app.services.detail_shape import build_detail_base
from app.redis import log_redis_degraded, redis_client
from app.services.category_service import is_known_category
from app.services.content_cache import node_stats_key
from app.services.content_cache import get_cached_list, list_cache_key, set_cached_list
from app.services.search import NODE_SEARCH_COLS, apply_keyword_filter, fetch_page
from app.services.view_counter import register_view

# 分类统计缓存 TTL（秒）+ 抖动：公开页每次加载执行的聚合查询，短缓存即可
_STATS_CACHE_TTL = 120
_STATS_CACHE_JITTER = 30


async def list_nodes(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 12,
    keyword: str = None,
    category: str = None,
    sort: str = "latest",
) -> dict:
    """节点列表

    sort 取值:
    - latest: 最新更新（按 updated_at 倒序）
    - hottest: 最热浏览（按 view_count 倒序）
    - recommended: 推荐（综合 view_count + id）
    """
    # 列表缓存：只缓存无关键词的浏览路径（搜索结果长尾、key 基数无上限）。
    # 分类还必须是 categories 表里登记过的值 —— 否则任意随机分类都会生成新 key，
    # 把 Redis 当垃圾桶（第二轮复审 C-05，详见 category_service.is_known_category）。
    cache_key = (
        None
        if keyword or not await is_known_category(db, "node", category)
        else list_cache_key("nodes", {"category": category, "sort": sort, "page": page, "page_size": page_size})
    )
    if cache_key:
        cached = await get_cached_list(cache_key)
        if cached is not None:
            return cached
    # 公开列表只出已发布内容：草稿（is_published=False）在后台可见可编辑，前台一律不出现
    conditions = [Node.is_published == True]  # noqa: E712
    if category and category != "all":
        conditions.append(Node.category == category)

    # 搜索：全文优先、LIKE 兜底 over name/description/content（与管理端共用同一构造器，
    # 第三轮增量审查 P1-41）。原改造前只对 name/description 做 LIKE，正文 content 搜不到 ——
    # 而「ComfyUI 显存不足怎么办」这类问题词恰恰出现在正文里。
    total = await apply_keyword_filter(db, Node, conditions, NODE_SEARCH_COLS, keyword)
    if not total:
        # 无命中就别再发分页查询：搜错词/无结果是最常见场景，而那条查询同样要做
        # 一次全表 LIKE 扫描（第五轮审查 PERF-01）
        payload = {"items": [], "total": 0, "page": page, "page_size": page_size}
        if cache_key:
            await set_cached_list(cache_key, payload)
        return payload

    # 列表仅使用小字段，load_only 避免 MEDIUMTEXT content 与 4 个 JSON 大字段传输
    query = (
        select(Node)
        .options(
            load_only(
                Node.id, Node.name, Node.category, Node.description,
                Node.view_count, Node.updated_at,
                Node.cover_image, Node.author_id, Node.author_name, Node.author_avatar_url,
            )
        )
        .where(*conditions)
    )
    if sort == "hottest":
        # 三个分支都以唯一列 `id` 收尾（第四轮审查 P2-30）：view_count / updated_at 会并列，
        # 无兜底时 MySQL 不保证两次查询同序 → 翻页重复或漏项
        query = query.order_by(Node.view_count.desc(), Node.updated_at.desc(), Node.id.desc())
    elif sort == "recommended":
        query = query.order_by(Node.view_count.desc(), Node.id.desc())
    else:
        query = query.order_by(Node.updated_at.desc(), Node.id.desc())

    query = query.offset((page - 1) * page_size).limit(page_size)
    nodes = await fetch_page(db, query, keyword=keyword)

    items = [
        {
            "id": n.id,
            "name": n.name,
            "category": n.category,
            "description": n.description,
            "view_count": n.view_count,
            "updated_at": n.updated_at.isoformat() if n.updated_at else None,
            # 封面 + 发布作者（2026-10-12 新增）：未填写则前台不渲染对应区域
            "cover_image": n.cover_image,
            "author_name": n.author_name,
            "author_avatar_url": n.author_avatar_url,
        }
        for n in nodes
    ]
    # 作者以账号当前昵称为准（改昵称/换头像要联动），账号已删则保留快照
    await apply_live_authors(db, nodes, items)
    payload = {"items": items, "total": total, "page": page, "page_size": page_size}
    if cache_key:
        await set_cached_list(cache_key, payload)
    return payload


async def get_node_stats(db: AsyncSession) -> dict:
    """节点分类统计（Redis 缓存 120s，Redis 故障降级直查 DB）"""
    cache_key = node_stats_key()
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        log_redis_degraded("node.stats_cache", "Redis 不可用，节点统计缓存降级直查 DB")

    # 动态从 categories 表查询 module='node' 的分类列表
    cat_rows = (
        await db.execute(
            select(Category.name)
            .where(Category.module == "node")
            .order_by(Category.sort_order.asc(), Category.id.asc())
        )
    ).scalars().all()
    category_labels = {name: name for name in cat_rows}

    # 按分类统计节点数。
    # ⚠️ 必须带 is_published：列表只出已发布内容（见上方 conditions），统计若不过滤，
    # 就会出现「分类 Tab 说 30 个、点进去 28 条」「首页总数比列表多」——
    # 下架内容后数字还不降（第二轮复审 C-03）。
    query = (
        select(Node.category, func.count(Node.id))
        .where(Node.is_published.is_(True))
        .group_by(Node.category)
    )
    rows = (await db.execute(query)).all()

    counts = {c: 0 for c in category_labels}
    for cat, count in rows:
        if cat in counts:
            counts[cat] = count
        else:
            # 兼容数据库中新增但未录入 categories 表的分类
            category_labels[cat] = cat
            counts[cat] = count

    total = sum(counts.values())
    result = {
        "total": total,
        "categories": [
            # 键名与 /resources/stats 对齐用 （第三轮增量审查 P1-48②）：
            # 原先一个用 type 一个用 key，前端得写两份映射；已核查无消费方。
            {"type": cat, "label": label, "count": counts[cat]}
            for cat, label in category_labels.items()
        ],
    }
    try:
        await redis_client.set(cache_key, json.dumps(result, ensure_ascii=False), ex=_STATS_CACHE_TTL + random.randint(0, _STATS_CACHE_JITTER))
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("node_service.get_node_stats", exc)
    return result


async def get_node_detail(db: AsyncSession, node_id: int) -> dict:
    """节点详情 - 自增浏览量"""
    # 草稿与「不存在」同样返回 404：否则拿到 ID 就能绕过列表直接看未发布内容
    node = (
        await db.execute(
            select(Node).where(Node.id == node_id, Node.is_published == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if not node:
        raise NotFoundError("节点不存在")

    # 浏览量：Redis 累加（一次往返同时得到「待落库增量」，响应值 = DB 值 + 增量），
    # 落库交给调度器每分钟合并；Redis 不可用时自动回退为直接 UPDATE，行为与改造前一致
    new_view_count = await register_view(db, "node", node.id, node.view_count)
    # 公共字段（id / title+name / description / category / 封面 / 发布作者 /
    # SEO / 浏览量 / 更新时间）由 build_detail_base 统一产出（EXT-04），
    # 这里只声明百科专有的键 —— 以后给「所有详情」加字段只改那一处
    data = build_detail_base(
        CONTENT_TYPES["node"],
        node,
        view_count=new_view_count,
        extra={
            "content": node.content,
            "inputs": node.inputs,
            "outputs": node.outputs,
            "params": node.params,
            "examples": node.examples,
        },
    )

    # 作者以账号当前昵称为准（改昵称/换头像要联动），账号已删则保留快照
    await apply_live_author_one(db, node, data)
    return data
