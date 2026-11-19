"""教程服务"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from loguru import logger

from app.models.tutorial import Tutorial
from app.models.user import User
from app.services.learning_service import upsert_learning_record
from app.services.tutorial_node_service import get_node_tree
from app.core.exceptions import NotFoundError, AppException
from app.core.content_types import CONTENT_TYPES
from app.services.detail_shape import build_detail_base
from app.services.search import TUTORIAL_SEARCH_COLS, apply_keyword_filter, fetch_page
from app.services.view_counter import register_view
from app.services.category_service import is_known_category
from app.services.content_cache import get_cached_list, list_cache_key, set_cached_list


async def get_tutorials(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 6,
    keyword: str = None,
    category: str = None,
    sort: str = "latest",
) -> dict:
    """获取教程列表"""
    # 列表仅使用小字段，load_only 避免 MEDIUMTEXT content/introduction 大字段传输
    query = (
        select(Tutorial)
        .options(
            load_only(
                Tutorial.id, Tutorial.title, Tutorial.description, Tutorial.category,
                Tutorial.cover_image, Tutorial.view_count, Tutorial.status,
                Tutorial.is_pinned, Tutorial.is_recommended, Tutorial.published_at,
            )
        )
    )

    # 列表缓存：只缓存无关键词的浏览路径（搜索结果长尾、key 基数无上限）。
    # 分类必须是登记过的值，否则任意随机分类都会生成新 key（C-05）。
    cache_key = (
        None
        if keyword or not await is_known_category(db, "tutorial", category)
        else list_cache_key("tutorials", {"category": category, "sort": sort, "page": page, "page_size": page_size})
    )
    if cache_key:
        cached = await get_cached_list(cache_key)
        if cached is not None:
            return cached
    # 基础筛选（发布状态 + 分类），列表与计数共用，避免两处条件走偏
    base_conditions = [Tutorial.is_published.is_(True)]
    if category and category != "all":
        base_conditions.append(Tutorial.category == category)

    # 搜索：全文优先、LIKE 兜底（与管理端共用同一构造器，第三轮增量审查 P1-41）。
    # 命中的关键词条件由 helper 追加进 base_conditions，供下方 where 一并使用。
    total = await apply_keyword_filter(db, Tutorial, base_conditions, TUTORIAL_SEARCH_COLS, keyword)
    if not total:
        # 无命中就别再发分页查询：搜错词/无结果是最常见场景，而那条查询同样要做
        # 一次全表 LIKE 扫描（第五轮审查 PERF-01）
        payload = {"items": [], "total": 0, "page": page, "page_size": page_size}
        if cache_key:
            await set_cached_list(cache_key, payload)
        return payload
    query = query.where(*base_conditions)

    # 排序：置顶 > 推荐 > 原有排序（最新/热门），与资源模块口径一致
    # ⚠️ 每个分支都以 `id.desc()` 收尾（第四轮审查 P2-30）：`published_at` / `view_count`
    #    都会并列（批量发布、同分浏览数），无唯一列兜底时 MySQL 不保证两次查询同序，
    #    翻页就会重复或漏项 —— 同 P2-8 分片、P2-36 那条教训。
    if sort == "latest":
        query = query.order_by(
            Tutorial.is_pinned.desc(), Tutorial.is_recommended.desc(),
            Tutorial.published_at.desc(), Tutorial.id.desc(),
        )
    elif sort == "hottest":
        query = query.order_by(
            Tutorial.is_pinned.desc(), Tutorial.is_recommended.desc(),
            Tutorial.view_count.desc(), Tutorial.id.desc(),
        )

    # 总数 - 直接对主表 count, 避免 subquery 包裹 (PERF)；已在上方与各候选条件一起算好
    # 分页
    query = query.offset((page - 1) * page_size).limit(page_size)
    tutorials = await fetch_page(db, query, keyword=keyword)

    items = [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "category": t.category,
            "cover_image": t.cover_image,
            "view_count": t.view_count,
            "status": t.status,
            "is_pinned": t.is_pinned,
            "is_recommended": t.is_recommended,
            "published_at": t.published_at.isoformat() if t.published_at else None,
        }
        for t in tutorials
    ]

    payload = {"items": items, "total": total, "page": page, "page_size": page_size}
    if cache_key:
        await set_cached_list(cache_key, payload)
    return payload


async def get_tutorial_intro(db: AsyncSession, tutorial_id: int, user: User | None = None) -> dict:
    """获取教程介绍页数据（公开）

    安全设计：本接口无需登录即可访问，因此：
    - 不返回章节正文（登录后经 /tutorials/{id}/nodes/{nid} 单章接口获取，
      避免 intro 成为绕过登录门槛的通道）
    - 课程目录为章节树小字段（id/title/children），来自 tutorial_nodes 表
    """
    query = (
        select(Tutorial)
        .options(
            # 响应不使用 content（章节化后正文在 tutorial_nodes 表），
            # load_only 裁剪 MEDIUMTEXT 大字段
            load_only(
                Tutorial.id, Tutorial.title, Tutorial.description, Tutorial.introduction,
                Tutorial.category, Tutorial.cover_image, Tutorial.view_count,
                Tutorial.status,
                Tutorial.is_recommended, Tutorial.published_at,
                Tutorial.seo_title, Tutorial.seo_description, Tutorial.seo_keywords,
                # ⚠️ updated_at 必须在这里：详情响应的公共字段（detail_shape）会读它，
                # 漏了会在异步上下文里触发**懒加载** → MissingGreenlet → 整页 500。
                # （2026-10-20 线上事故：教程详情/介绍页全部 500，测试用假 sqlite 查不出来）
                Tutorial.updated_at,
            ),
        )
        .where(Tutorial.id == tutorial_id, Tutorial.is_published.is_(True))
    )
    result = await db.scalars(query)
    tutorial = result.one_or_none()

    if not tutorial:
        raise NotFoundError("教程不存在")

    # 当前用户是否可读正文（需登录；正文在单章接口下发）
    can_read = bool(user)

    # 目录从章节表生成（章节化改造后 content 不再是目录数据源）
    node_tree = await get_node_tree(db, tutorial_id)

    # 公共字段由 build_detail_base 统一产出（EXT-04），这里只声明教程专有的键
    return build_detail_base(
        CONTENT_TYPES["tutorial"],
        tutorial,
        extra={
            "introduction": tutorial.introduction,
            "status": tutorial.status,
            "is_recommended": tutorial.is_recommended,
            "published_at": tutorial.published_at.isoformat() if tutorial.published_at else None,
            "can_read": can_read,
            "nodes": node_tree["nodes"],
            # flat 一并返回：阅读页原先要额外请求 /tutorials/{id}/nodes 才能拿到前序列表，
            # 而 intro 已经构建过同一棵树（含 flat），再让前端拉一次等于把同一棵树查两遍
            "flat": node_tree["flat"],
            "node_count": len(node_tree["flat"]),
        },
    )


async def get_tutorial_detail(
    db: AsyncSession, tutorial_id: int, user: User | None = None
) -> dict:
    """获取教程详情（返回完整正文，前端整篇连续渲染）

    user 传入时在同一次行加载内完成阅读权限校验，
    替代路由层「权限投影查询 + 整行再查」的重复查询（PERF）。
    """
    # 只取教程行本身；阅读权限 = 登录即可（校验在下方完成），无需关联表
    query = select(Tutorial).where(
        Tutorial.id == tutorial_id,
        Tutorial.is_published.is_(True),
    )
    tutorial = (await db.scalars(query)).one_or_none()

    if not tutorial:
        raise NotFoundError("教程不存在")

    # 阅读权限：所有教程（含免费）都需登录才返回正文
    if user is None:
        raise AppException(code=401, message="请先登录后阅读教程")

    # 浏览量：Redis 累加（DB 值 + 待落库增量），落库由调度器每分钟合并；
    # view_count 兜底 or 0：历史数据/手工改库可能为 NULL
    view_count = await register_view(db, "tutorial", tutorial.id, tutorial.view_count)

    # 公共字段由 build_detail_base 统一产出（EXT-04），这里只声明整篇详情专有的键
    return build_detail_base(
        CONTENT_TYPES["tutorial"],
        tutorial,
        view_count=view_count,
        extra={
            "content": tutorial.content,
            "status": tutorial.status,
            "published_at": tutorial.published_at.isoformat() if tutorial.published_at else None,
        },
    )


async def track_learning(db: AsyncSession, user: User, tutorial_id: int, duration: int) -> dict:
    """上报学习时长（纯统计）

    同步学习记录（用于"继续学习"与统计），savepoint 包裹并发首报异常
    """
    # 教程存在性校验由 upsert_learning_record 内的单次查询完成，无需重复预检。
    # savepoint 包裹：并发首报撞 uq_user_tutorial 唯一约束等 DB 异常仅回滚本段
    # 插入，不污染请求事务
    try:
        async with db.begin_nested():
            await upsert_learning_record(db, user, tutorial_id)
    except NotFoundError:
        raise  # 教程不存在需向前端返回 404，不得降级
    except Exception as e:  # noqa: BLE001 - 学习记录写入失败不应阻断响应
        logger.warning("upsert_learning_record 失败: {}", e)

    return {"duration": min(duration, 15)}  # 限制单次最大15秒


# 阅读权限（登录即可读）

async def check_tutorial_read_access(
    db: AsyncSession,
    user: User | None,
    tutorial_id: int,
) -> None:
    """教程正文阅读权限校验（登录即可读），不通过抛出 401。"""
    if user is None:
        raise AppException(code=401, message="请先登录后阅读教程")
