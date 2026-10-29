"""资源服务"""

import json
import random
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only
from loguru import logger

from app.models.resource import Resource
from app.models.download import Download
from app.models.user import User
from app.services.content_author import (
    apply_live_author_one,
    apply_live_authors,
)
from app.core.exceptions import NotFoundError
from app.core.content_types import CONTENT_TYPES
from app.services.detail_shape import build_detail_base
from app.redis import log_redis_degraded, redis_client
from app.services.category_service import is_known_category
from app.services.content_cache import (
    resource_hot_key,
    resource_latest_key,
    resource_stats_key,
)
from app.services.content_cache import get_cached_list, list_cache_key, set_cached_list
from app.utils.sanitize import escape_like
from app.services.search import RESOURCE_SEARCH_COLS, apply_keyword_filter, fetch_page
from app.services.view_counter import register_view


# 列表缓存 TTL 抖动上限（秒）：同批 key 同时写入时若 TTL 固定会同时过期，
# 过期瞬间并发请求全部回源 DB（缓存雪崩），加随机抖动错峰过期
_CACHE_TTL_JITTER = 60


# 文件名后缀 -> file_type 映射
_FILE_TYPE_MAP = [
    ((".pdf",), "pdf"),
    ((".zip", ".rar", ".7z", ".tar", ".gz"), "archive"),
    ((".exe", ".msi", ".dmg", ".apk"), "exe"),
]


def detect_file_type(filename: str | None) -> str:
    """根据文件名后缀判断类型"""
    if not filename:
        return "other"
    lower = filename.lower()
    for suffixes, ftype in _FILE_TYPE_MAP:
        if any(lower.endswith(s) for s in suffixes):
            return ftype
    return "other"


async def get_resources(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 8,
    keyword: str = None,
    resource_type: str = None,
    sort_by: str = None,
) -> dict:
    """获取资源列表"""
    # 基础筛选（类型），列表与计数共用
    # 公开列表只出已发布内容（同 node_service）
    # 列表缓存：只缓存无关键词的浏览路径（搜索结果长尾、key 基数无上限）。
    # 资源类型必须是登记过的值，否则任意随机类型都会生成新 key（C-05）。
    cache_key = (
        None
        if keyword or not await is_known_category(db, "resource", resource_type)
        else list_cache_key("resources", {"resource_type": resource_type, "sort_by": sort_by, "page": page, "page_size": page_size})
    )
    if cache_key:
        cached = await get_cached_list(cache_key)
        if cached is not None:
            return cached
    conditions = [Resource.is_published == True]  # noqa: E712
    if resource_type and resource_type != "all":
        conditions.append(Resource.resource_type == resource_type)

    # 搜索：全文优先、LIKE 兜底（与管理端共用同一构造器，第三轮增量审查 P1-41）
    total = await apply_keyword_filter(db, Resource, conditions, RESOURCE_SEARCH_COLS, keyword)
    if not total:
        # 无命中就别再发分页查询：搜错词/无结果是最常见场景，而那条查询同样要做
        # 一次全表 LIKE 扫描（第五轮审查 PERF-01）
        payload = {"items": [], "total": 0, "page": page, "page_size": page_size}
        if cache_key:
            await set_cached_list(cache_key, payload)
        return payload

    # 列表未使用 content/usage_notes (TEXT)，load_only 避免大字段传输
    query = (
        select(Resource)
        .options(
            load_only(
                Resource.id, Resource.name, Resource.description, Resource.resource_type,
                Resource.file_size, Resource.content_list, Resource.download_count,
                Resource.view_count, Resource.is_featured, Resource.is_pinned,
                Resource.cover_image, Resource.tags, Resource.updated_at,
                Resource.author_id, Resource.author_name, Resource.author_avatar_url,
            )
        )
        .where(*conditions)
    )
    # 置顶 > 推荐 > 原有排序（最新/热门）
    # ⚠️ 以 `id.desc()` 收尾：`updated_at` / `download_count` 会并列，无唯一列兜底时
    #    MySQL 不保证两次查询同序，翻页会重复或漏项（第四轮审查 P2-30，同 P2-8/P2-36）
    if sort_by == "download_count":
        query = query.order_by(
            Resource.is_pinned.desc(), Resource.is_featured.desc(),
            Resource.download_count.desc(), Resource.updated_at.desc(), Resource.id.desc(),
        )
    else:
        query = query.order_by(
            Resource.is_pinned.desc(), Resource.is_featured.desc(),
            Resource.updated_at.desc(), Resource.id.desc(),
        )
    query = query.offset((page - 1) * page_size).limit(page_size)
    resources = await fetch_page(db, query, keyword=keyword)

    items = [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "resource_type": r.resource_type,
            "file_size": r.file_size,
            "content_list": r.content_list,
            "download_count": r.download_count,
            "view_count": r.view_count,
            "is_featured": r.is_featured,
            "is_pinned": r.is_pinned,
            "cover_image": r.cover_image,
            # 发布作者：为空时前台不渲染作者行（NULL = 未填写）
            "author_name": r.author_name,
            "author_avatar_url": r.author_avatar_url,
            "tags": r.tags,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in resources
    ]
    # 作者以账号当前昵称为准（改昵称/换头像要联动），账号已删则保留快照
    await apply_live_authors(db, resources, items)

    payload = {"items": items, "total": total, "page": page, "page_size": page_size}
    if cache_key:
        await set_cached_list(cache_key, payload)
    return payload


async def get_resource_detail(db: AsyncSession, resource_id: int, user: User | None = None) -> dict:
    """获取资源详情 (游客不含网盘链接)"""
    query = (
        select(Resource)
        .options(selectinload(Resource.netdisk_links))
        .where(Resource.id == resource_id, Resource.is_published == True)  # noqa: E712
    )
    result = await db.execute(query)
    resource = result.scalar_one_or_none()

    if not resource:
        raise NotFoundError("资源不存在")

    # 网盘链接: 仅登录用户可见
    netdisk_links = []
    if user:
        netdisk_links = [
            {
                "id": link.id,
                "platform": link.platform,
                "url": link.url,
                "extract_code": link.extract_code,
            }
            for link in sorted(resource.netdisk_links, key=lambda x: x.sort_order)
        ]

    # 浏览量：Redis 累加（详见 view_counter 模块说明），Redis 不可用时回退直接写库
    new_view_count = await register_view(db, "resource", resource.id, resource.view_count)
    # 公共字段由 build_detail_base 统一产出（EXT-04），这里只声明资源专有的键。
    # `category` 由注册表的 category_attr 归一成 resource_type 的值，
    # 原 `resource_type` 键保留（前端在用），两者恒等。
    data = build_detail_base(
        CONTENT_TYPES["resource"],
        resource,
        view_count=new_view_count,
        extra={
            "content": resource.content,
            "resource_type": resource.resource_type,
            "file_size": resource.file_size,
            "content_list": resource.content_list,
            "usage_notes": resource.usage_notes,
            "download_count": resource.download_count,
            "is_featured": resource.is_featured,
            "is_pinned": resource.is_pinned,
            "tags": resource.tags,
            "netdisk_links": netdisk_links,
            # 链接**数量**对游客也返回（与 tutorial_service 的 netdisk_link_count 同一约定）：
            # 前台要据此决定「下载链接」Tab 是否渲染。只给 links 是不够的——游客拿到的
            # 永远是空数组，前端无法区分「这条资源没有链接」和「你没登录所以看不到」，
            # 于是要么给所有资源都显示一个空 Tab，要么把有链接的资源也藏掉。
            "netdisk_link_count": len(resource.netdisk_links),
        },
    )

    # 作者以账号当前昵称为准（改昵称/换头像要联动），账号已删则保留快照
    await apply_live_author_one(db, resource, data)
    return data


async def get_download_links(db: AsyncSession, user: User, resource_id: int) -> dict:
    """获取网盘下载链接 (登录后可调用, 记录下载历史)"""
    query = (
        select(Resource)
        .options(selectinload(Resource.netdisk_links))
        .where(Resource.id == resource_id, Resource.is_published == True)  # noqa: E712
    )
    result = await db.execute(query)
    resource = result.scalar_one_or_none()

    if not resource:
        raise NotFoundError("资源不存在")

    # 先读取需要的字段（UPDATE 前读取，避免 ORM 属性过期）
    resource_name = resource.name
    file_size = resource.file_size

    # 记录下载历史（含名称、文件类型快照）
    download = Download(
        user_id=user.id,
        resource_id=resource_id,
        resource_name=resource_name,
        file_size=file_size,
        file_type=detect_file_type(resource_name),
    )
    db.add(download)

    # 单条 UPDATE 自增下载次数（避免 ORM flush 的热点行锁竞争，与 tutorial_service 一致）
    await db.execute(
        update(Resource)
        .where(Resource.id == resource_id, Resource.is_published == True)  # noqa: E712
        .values(download_count=Resource.download_count + 1)
    )

    logger.info(f"用户 {user.id} 获取资源链接: {resource_name}")

    links = [
        {
            "id": link.id,
            "platform": link.platform,
            "url": link.url,
            "extract_code": link.extract_code,
        }
        for link in sorted(resource.netdisk_links, key=lambda x: x.sort_order)
    ]

    return {"links": links}


async def get_resource_stats(db: AsyncSession) -> dict:
    """获取资源分类统计（Redis 缓存 120s，Redis 故障降级直查 DB）"""
    cache_key = resource_stats_key()
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        log_redis_degraded("resource.stats_cache", "Redis 不可用，资源统计缓存降级直查 DB")

    # 按类型统计资源数。**必须带 is_published**：列表只出已发布内容，
    # 统计不过滤会让「分类数相加 ≠ 列表 total」，且下架后数字不降（第二轮复审 C-03）。
    query = (
        select(Resource.resource_type, func.count(Resource.id))
        .where(Resource.is_published.is_(True))
        .group_by(Resource.resource_type)
    )
    result = await db.execute(query)
    rows = result.all()

    type_labels = {
        "model": "AI模型",
        "tool": "工具软件",
        "dataset": "数据集",
        "source": "源码项目",
        "doc": "学习资料",
        "other": "其他资源",
    }
    counts = {rtype: 0 for rtype in type_labels}
    for rtype, count in rows:
        if rtype in counts:
            counts[rtype] = count
        else:
            # 兼容管理端自由填写的 resource_type：admin_resources.py:36 是无枚举校验的
            # 自由字符串，下拉项又取自 categories 表 → 运营加一个分类就能写入新取值。
            # 不登记的后果不是报错，而是该分类的资源**不进 categories**，却仍算进
            # total（counts 多出一个键）→ 分类数相加 ≠ 总数，前台那批资源无处显示。
            # 与 node_service.get_node_stats 的容错分支对齐（第四轮全量审查 P0-14）。
            type_labels[rtype] = rtype
            counts[rtype] = count

    total = sum(counts.values())
    stats = {
        "total": total,
        "categories": [
            {"type": rtype, "label": label, "count": counts[rtype]}
            for rtype, label in type_labels.items()
        ],
    }
    try:
        await redis_client.set(cache_key, json.dumps(stats, ensure_ascii=False), ex=120 + random.randint(0, _CACHE_TTL_JITTER))
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("resource_service.get_resource_stats", exc)
    return stats


async def get_hot_resources(db: AsyncSession, limit: int = 8) -> dict:
    """获取热门下载资源，返回 `{"items": [...]}`（与 /plugins/featured 同形）"""
    cache_key = resource_hot_key(limit)
    # Redis 故障降级直查 DB（缓存层故障不应放大为公开接口 500，与 home.py 策略一致）
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        log_redis_degraded("resource.hot_cache", "Redis 不可用，热门资源缓存降级直查 DB")

    query = (
        select(Resource)
        .options(
            load_only(
                Resource.id, Resource.name, Resource.description, Resource.resource_type,
                Resource.file_size, Resource.download_count, Resource.updated_at,
            )
        )
        .where(Resource.is_published == True)  # noqa: E712
        .order_by(Resource.download_count.desc(), Resource.updated_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    resources = result.scalars().all()
    items = [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "resource_type": r.resource_type,
            "file_size": r.file_size,
            "download_count": r.download_count,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in resources
    ]
    try:
        await redis_client.set(cache_key, json.dumps({"items": items}, ensure_ascii=False), ex=300 + random.randint(0, _CACHE_TTL_JITTER))
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("resource_service.get_hot_resources", exc)
    # ⚠️ 与 /plugins/featured 同形（第三轮增量审查 P1-48①）：原先返回**裸数组**，
    # 而 featured 返回 {"items": [...]} —— 同项目两种形状，前端每次都要先猜。
    # 已核查无消费方（Web 端不调用；小程序 utils/api.js 定义了 helper 但从未被调用）。
    return {"items": items}


async def get_latest_resources(db: AsyncSession, limit: int = 5) -> dict:
    """获取最新资源，返回 `{"items": [...]}`（与 /plugins/featured 同形）"""
    cache_key = resource_latest_key(limit)
    # Redis 故障降级直查 DB（同 get_hot_resources）
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        log_redis_degraded("resource.latest_cache", "Redis 不可用，最新资源缓存降级直查 DB")

    query = (
        select(Resource)
        .options(
            load_only(
                Resource.id, Resource.name, Resource.resource_type,
                Resource.file_size, Resource.download_count, Resource.updated_at,
            )
        )
        .where(Resource.is_published == True)  # noqa: E712
        .order_by(Resource.updated_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    resources = result.scalars().all()
    items = [
        {
            "id": r.id,
            "name": r.name,
            "resource_type": r.resource_type,
            "file_size": r.file_size,
            "download_count": r.download_count,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in resources
    ]
    try:
        await redis_client.set(cache_key, json.dumps({"items": items}, ensure_ascii=False), ex=300 + random.randint(0, _CACHE_TTL_JITTER))
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("resource_service.get_latest_resources", exc)
    # ⚠️ 与 /plugins/featured 同形（第三轮增量审查 P1-48①）：原先返回**裸数组**，
    # 而 featured 返回 {"items": [...]} —— 同项目两种形状，前端每次都要先猜。
    # 已核查无消费方（Web 端不调用；小程序 utils/api.js 定义了 helper 但从未被调用）。
    return {"items": items}


async def get_download_history(
    db: AsyncSession,
    user: User,
    page: int = 1,
    page_size: int = 20,
    keyword: str = None,
    file_type: str = None,
) -> dict:
    """获取下载历史（支持 keyword / file_type 后端筛选）"""
    # 筛选条件只构造一次，data 与 count 查询复用，避免手工同步两份条件
    conditions = [Download.user_id == user.id]
    if keyword:
        conditions.append(
            Download.resource_name.contains(escape_like(keyword), escape="\\")
        )
    if file_type and file_type != "all":
        conditions.append(Download.file_type == file_type)

    # 只保留资源仍公开可见的记录：资源被删除或取消发布后，历史条目点进去只会提示「不存在」。
    # 内连接 + 发布态条件，count 与 data 共用同一套条件（下方 count 也走同一 join）。
    conditions.append(Resource.is_published.is_(True))

    total = (
        await db.execute(
            select(func.count(Download.id))
            .join(Resource, Download.resource_id == Resource.id)
            .where(*conditions)
        )
    ).scalar()

    query = (
        select(Download, Resource.name)
        .join(Resource, Download.resource_id == Resource.id)
        .where(*conditions)
        .order_by(Download.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.all()

    items = [
        {
            "id": download.id,
            "resource_id": download.resource_id,
            "resource_name": download.resource_name or resource_name,
            "file_size": download.file_size,
            "file_type": download.file_type or detect_file_type(download.resource_name or resource_name),
            "created_at": download.created_at.isoformat() if download.created_at else None,
        }
        for download, resource_name in rows
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}
