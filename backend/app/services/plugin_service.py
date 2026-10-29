"""扩展推荐服务（公开端）

从 api/plugins.py 抽取的 ORM 操作。保持与原路由完全相同的响应格式。
"""

import json
import random
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only

from app.models.node import Plugin
from app.core.exceptions import NotFoundError
from app.core.content_types import CONTENT_TYPES
from app.services.detail_shape import build_detail_base
from app.redis import log_redis_degraded, redis_client
from app.services.category_service import is_known_category
from app.services.content_cache import plugin_featured_key
from app.services.content_cache import get_cached_list, list_cache_key, set_cached_list
from app.services.content_author import (
    apply_live_author_one,
    apply_live_authors,
)
from app.services.search import PLUGIN_SEARCH_COLS, apply_keyword_filter, fetch_page
from app.services.view_counter import register_view

# 列表缓存 TTL 抖动上限（秒）：防同批 key 同时过期引发缓存雪崩（见 resource_service）
_CACHE_TTL_JITTER = 60

# 已下线的历史排序取值 → 现在等价于按 view_count 排。
# 存在理由：这些取值仍会从**已发布、无法强制更新**的客户端发来（小程序包由微信审核节奏
# 决定何时替换），直接 422 会让旧用户一点「最多使用」就看到空列表（第五轮审查 R5-04）。
# 摘除条件：路由层 `event=plugin_legacy_sort` 日志连续 7 天为 0。
LEGACY_SORT_ALIASES = frozenset({"hottest", "rating"})


async def list_plugins(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 8,
    keyword: str = None,
    category: str = None,
    sort: str = "comprehensive",
) -> dict:
    """扩展列表"""
    # 列表缓存：只缓存无关键词的浏览路径（搜索结果长尾、key 基数无上限）。
    # 分类必须是登记过的值，否则任意随机分类都会生成新 key（C-05）。
    cache_key = (
        None
        if keyword or not await is_known_category(db, "plugin", category)
        else list_cache_key("plugins", {"category": category, "sort": sort, "page": page, "page_size": page_size})
    )
    if cache_key:
        cached = await get_cached_list(cache_key)
        if cached is not None:
            return cached
    # 公开列表只出已发布内容（同 node_service）
    conditions = [Plugin.is_published == True]  # noqa: E712
    if category and category != "all":
        conditions.append(Plugin.category == category)

    # 搜索：全文优先、LIKE 兜底 over name/description/content（与管理端共用同一构造器，
    # 第三轮增量审查 P1-41 —— 先前管理端只搜 name，导致两边行为漂移）
    total = await apply_keyword_filter(db, Plugin, conditions, PLUGIN_SEARCH_COLS, keyword)
    if not total:
        # 无命中就别再发分页查询：搜错词/无结果是最常见场景，而那条查询同样要做
        # 一次全表 LIKE 扫描（第五轮审查 PERF-01）
        payload = {"items": [], "total": 0, "page": page, "page_size": page_size}
        if cache_key:
            await set_cached_list(cache_key, payload)
        return payload

    # 列表未使用 content (MEDIUMTEXT)，load_only 避免大字段传输
    query = (
        select(Plugin)
        .options(
            load_only(
                Plugin.id, Plugin.name, Plugin.category, Plugin.view_count,
                Plugin.description, Plugin.created_at, Plugin.updated_at,
                Plugin.author_id, Plugin.author_name, Plugin.author_avatar_url,
            )
        )
        .where(*conditions)
    )
    # ⚠️ 两个分支都以 `id.desc()` 收尾（第四轮审查 P2-30）：view_count / created_at
    #    都会并列，无唯一列兜底时 MySQL 不保证两次查询同序 → 翻页重复或漏项
    if sort == "latest":
        query = query.order_by(Plugin.created_at.desc(), Plugin.id.desc())
    else:
        # comprehensive（默认）以及**历史别名的落点**：按预览量排。
        # 2026-10-12 之前这里排的是 rating + install_count，两列已随迁移 0040 删除；
        # `hottest` / `rating` 两个旧取值由路由层放行后走到这里（别名表见上），
        # 语义变成「按预览量排」—— 排序键变了但不报错，为的是不让旧小程序包整页 422（R5-04）。
        query = query.order_by(Plugin.view_count.desc(), Plugin.id.desc())

    query = query.offset((page - 1) * page_size).limit(page_size)
    plugins = await fetch_page(db, query, keyword=keyword)

    items = [
        {
            "id": p.id, "name": p.name, "category": p.category,
            "view_count": p.view_count,
            "description": p.description,
            # 发布作者：为空时前台不渲染作者行（NULL = 未填写）
            "author_name": p.author_name,
            "author_avatar_url": p.author_avatar_url,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in plugins
    ]
    # 作者以账号当前昵称为准（改昵称/换头像要联动），账号已删则保留快照
    await apply_live_authors(db, plugins, items)
    payload = {"items": items, "total": total, "page": page, "page_size": page_size}
    if cache_key:
        await set_cached_list(cache_key, payload)
    return payload


async def get_featured_plugins(db: AsyncSession, limit: int = 12) -> dict:
    """推荐扩展轮播 - 取预览量前 N 条（原按 rating + install_count，两列已删）"""
    cache_key = plugin_featured_key(limit)
    # Redis 故障降级直查 DB（缓存层故障不应放大为公开接口 500，与 home.py 策略一致）
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        log_redis_degraded("plugin.featured_cache", "Redis 不可用，精选扩展缓存降级直查 DB")

    query = (
        select(Plugin)
        .options(
            load_only(
                Plugin.id, Plugin.name, Plugin.category,
                Plugin.view_count, Plugin.description,
                Plugin.author_id, Plugin.author_name, Plugin.author_avatar_url,
            )
        )
        .where(Plugin.is_published == True)  # noqa: E712
        .order_by(Plugin.view_count.desc(), Plugin.id.desc())
        .limit(limit)
    )
    plugins = (await db.execute(query)).scalars().all()
    items = [
        {
            "id": p.id, "name": p.name, "category": p.category,
            "view_count": p.view_count,
            "description": p.description,
            "author_name": p.author_name,
            "author_avatar_url": p.author_avatar_url,
            # 原先这里还多写了一个 `"cover_image": None` —— `Plugin` 表根本没有这一列
            # （第五轮全量审查 P1-77 的「featured 多出 list/detail 都没有的幽灵键」）。
            # 三端实测零消费方（用户端/管理端不读，小程序只有 api.js 里一个从未被调用的
            # `getFeaturedPlugins`），留着只会让人以为扩展有封面图，故删除。
        }
        for p in plugins
    ]
    # 作者以账号当前昵称为准（改昵称/换头像要联动），账号已删则保留快照
    await apply_live_authors(db, plugins, items)
    result = {"items": items}
    try:
        await redis_client.set(cache_key, json.dumps(result, ensure_ascii=False), ex=300 + random.randint(0, _CACHE_TTL_JITTER))
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("plugin_service.get_featured_plugins", exc)
    return result


async def get_plugin_detail(db: AsyncSession, plugin_id: int) -> dict:
    """扩展详情 - 自增浏览量"""
    query = (
        select(Plugin)
        .options(selectinload(Plugin.sources))
        .where(Plugin.id == plugin_id, Plugin.is_published == True)  # noqa: E712
    )
    plugin = (await db.execute(query)).scalar_one_or_none()
    if not plugin:
        raise NotFoundError("扩展不存在")

    # 浏览量：Redis 累加（详见 view_counter 模块说明），Redis 不可用时回退直接写库
    new_view_count = await register_view(db, "plugin", plugin.id, plugin.view_count)
    sources = [
        {"id": s.id, "source_name": s.source_name, "url": s.url, "extract_code": s.extract_code}
        for s in plugin.sources
    ]
    # 公共字段由 build_detail_base 统一产出（EXT-04），这里只声明扩展专有的键
    data = build_detail_base(
        CONTENT_TYPES["plugin"],
        plugin,
        view_count=new_view_count,
        extra={"content": plugin.content, "sources": sources},
    )

    # 作者以账号当前昵称为准（改昵称/换头像要联动），账号已删则保留快照
    await apply_live_author_one(db, plugin, data)
    return data
