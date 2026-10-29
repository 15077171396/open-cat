"""教程服务"""

from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only
from loguru import logger

from app.models.tutorial import Tutorial, TutorialPurchase
from app.models.user import User
from app.services.timer_service import check_and_consume, is_vip_effective
from app.services.learning_service import upsert_learning_record
from app.services.tutorial_node_service import get_node_tree
from app.services.point_service import record_transaction, TYPE_CONSUME
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
                Tutorial.access_mode, Tutorial.points_price,
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
    base_conditions = [Tutorial.is_published == True]  # noqa: E712
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
            "access_mode": t.access_mode,
            "points_price": t.points_price,
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
    - 网盘链接（含提取码）仅对具备阅读权限的用户可见（免费模式需登录，
      会员模式需 VIP 有效或已积分购买），其余仅返回数量
    """
    query = (
        select(Tutorial)
        .options(
            selectinload(Tutorial.netdisk_links),
            # 响应不使用 content（章节化后正文在 tutorial_nodes 表），
            # load_only 裁剪 MEDIUMTEXT 大字段
            load_only(
                Tutorial.id, Tutorial.title, Tutorial.description, Tutorial.introduction,
                Tutorial.category, Tutorial.cover_image, Tutorial.view_count,
                Tutorial.status, Tutorial.access_mode, Tutorial.points_price,
                Tutorial.is_recommended, Tutorial.published_at,
                Tutorial.seo_title, Tutorial.seo_description, Tutorial.seo_keywords,
                # ⚠️ updated_at 必须在这里：详情响应的公共字段（detail_shape）会读它，
                # 漏了会在异步上下文里触发**懒加载** → MissingGreenlet → 整页 500。
                # （2026-10-20 线上事故：教程详情/介绍页全部 500，测试用假 session 查不出来）
                Tutorial.updated_at,
            ),
        )
        .where(Tutorial.id == tutorial_id, Tutorial.is_published == True)  # noqa: E712
    )
    result = await db.execute(query)
    tutorial = result.scalar_one_or_none()

    if not tutorial:
        raise NotFoundError("教程不存在")

    # 当前用户是否可读正文（免费模式已登录即可；会员模式需 VIP 有效或已购买）。
    # is_vip_effective 按到期时间即时判定，避免过期 VIP 的权益滞留。
    purchased = bool(
        user and tutorial.access_mode == "vip" and await has_purchased(db, user.id, tutorial_id)
    )
    can_read = bool(
        user
        and (
            tutorial.access_mode != "vip"
            or is_vip_effective(user)
            or purchased
        )
    )

    # 目录从章节表生成（章节化改造后 content 不再是目录数据源）
    node_tree = await get_node_tree(db, tutorial_id)

    netdisk_links = [
        {
            "id": link.id,
            "platform": link.platform,
            "url": link.url,
            "extract_code": link.extract_code,
        }
        for link in tutorial.netdisk_links
    ] if can_read else []

    # 公共字段由 build_detail_base 统一产出（EXT-04），这里只声明教程专有的键
    return build_detail_base(
        CONTENT_TYPES["tutorial"],
        tutorial,
        extra={
            "introduction": tutorial.introduction,
            "status": tutorial.status,
            "access_mode": tutorial.access_mode,
            "points_price": tutorial.points_price,
            "is_recommended": tutorial.is_recommended,
            "published_at": tutorial.published_at.isoformat() if tutorial.published_at else None,
            "can_read": can_read,
            "purchased": purchased,
            "nodes": node_tree["nodes"],
            # flat 一并返回：阅读页原先要额外请求 /tutorials/{id}/nodes 才能拿到前序列表，
            # 而 intro 已经构建过同一棵树（含 flat），再让前端拉一次等于把同一棵树查两遍
            "flat": node_tree["flat"],
            "node_count": len(node_tree["flat"]),
            "netdisk_links": netdisk_links,
            "netdisk_link_count": len(tutorial.netdisk_links),
        },
    )


async def get_tutorial_detail(
    db: AsyncSession, tutorial_id: int, user: User | None = None
) -> dict:
    """获取教程详情（返回完整正文，前端整篇连续渲染）

    user 传入时在同一次行加载内完成阅读权限校验（复用已加载的
    access_mode/points_price），替代路由层「权限投影查询 + 整行再查」
    的重复查询（PERF）。
    """
    query = (
        select(Tutorial)
        .options(selectinload(Tutorial.netdisk_links))
        .where(Tutorial.id == tutorial_id, Tutorial.is_published == True)  # noqa: E712
    )
    result = await db.execute(query)
    tutorial = result.scalar_one_or_none()

    if not tutorial:
        raise NotFoundError("教程不存在")

    # 权限校验复用本行字段，不再重复投影查询
    if user is not None:
        await check_tutorial_read_access(
            db, user, tutorial_id,
            access_mode=tutorial.access_mode,
            points_price=tutorial.points_price,
        )
    else:
        # 与 intro / 单章接口同口径：**任何**教程（含免费）都需登录才返回正文与网盘链接。
        # 此前只对 access_mode=="vip" 拦，免费教程的正文和提取码会被游客直接拿走，
        # 而同一教程的 /intro 对游客是隐藏链接的 —— 两个接口三套口径。
        raise AppException(code=401, message="请先登录后学习教程")

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
            "access_mode": tutorial.access_mode,
            "points_price": tutorial.points_price,
            "published_at": tutorial.published_at.isoformat() if tutorial.published_at else None,
            "netdisk_links": [
                {
                    "id": link.id,
                    "platform": link.platform,
                    "url": link.url,
                    "extract_code": link.extract_code,
                }
                for link in tutorial.netdisk_links
            ],
        },
    )


async def track_learning(db: AsyncSession, user: User, tutorial_id: int, duration: int) -> dict:
    """上报学习时长（纯统计，不再作为阅读门槛）

    - 同步学习记录（用于"继续学习"与统计），savepoint 包裹并发首报异常
    - 累计实际学习时长统计（free_used_seconds），免费自然时长已不限制教程阅读
    """
    # 教程存在性校验由 upsert_learning_record 内的单次查询完成，无需重复预检。
    # savepoint 包裹：并发首报撞 uq_user_tutorial 唯一约束等 DB 异常仅回滚本段
    # 插入，不污染请求事务（否则 session 进入 PendingRollback 状态，
    # 后续 check_and_consume 的 flush 必然 500）
    try:
        async with db.begin_nested():
            await upsert_learning_record(db, user, tutorial_id)
    except NotFoundError:
        raise  # 教程不存在需向前端返回 404，不得降级
    except Exception as e:  # noqa: BLE001 - 学习记录写入失败不应阻断计费
        logger.warning("upsert_learning_record 失败: {}", e)

    # 实际学习时长统计（VIP 不限时；普通用户仅累计统计，不再因耗尽被拦截）
    return await check_and_consume(user, db, min(duration, 15))  # 限制单次最大15秒


# 阅读权限（免费 / 会员 / 积分购买）

async def has_purchased(db: AsyncSession, user_id: int, tutorial_id: int) -> bool:
    """用户是否已积分购买该教程（购买后永久可读）"""
    purchase_id = (
        await db.execute(
            select(TutorialPurchase.id).where(
                TutorialPurchase.user_id == user_id,
                TutorialPurchase.tutorial_id == tutorial_id,
            )
        )
    ).scalar_one_or_none()
    return purchase_id is not None


async def list_purchased_tutorials(
    db: AsyncSession, user_id: int, page: int = 1, page_size: int = 10,
) -> dict:
    """用户已购买的教程列表（购买时间倒序，个人中心「购买的课程」页签数据源）"""
    conditions = [
        TutorialPurchase.user_id == user_id,
        Tutorial.is_published == True,  # noqa: E712
    ]
    total = (
        await db.execute(
            select(func.count(TutorialPurchase.id))
            .join(Tutorial, Tutorial.id == TutorialPurchase.tutorial_id)
            .where(*conditions)
        )
    ).scalar()
    # 列表仅用小字段，load_only 避免 MEDIUMTEXT content/introduction 大字段传输
    rows = (
        await db.execute(
            select(Tutorial, TutorialPurchase)
            .options(load_only(
                Tutorial.id, Tutorial.title, Tutorial.description, Tutorial.category,
                Tutorial.cover_image, Tutorial.view_count, Tutorial.status,
                Tutorial.access_mode, Tutorial.points_price, Tutorial.published_at,
            ))
            .join(Tutorial, Tutorial.id == TutorialPurchase.tutorial_id)
            .where(*conditions)
            .order_by(TutorialPurchase.created_at.desc(), TutorialPurchase.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "category": t.category,
            "cover_image": t.cover_image,
            "view_count": t.view_count,
            "status": t.status,
            "access_mode": t.access_mode,
            "points_price": t.points_price,
            "purchased_at": p.created_at.isoformat() if p.created_at else None,
            "points_paid": p.points_paid,
        }
        for t, p in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def check_tutorial_read_access(
    db: AsyncSession,
    user: User | None,
    tutorial_id: int,
    *,
    access_mode: str | None = None,
    points_price: int | None = None,
) -> None:
    """教程正文阅读权限校验，不通过抛出 404/401/403。

    权限模型（均需登录）：
    - free：登录即可读
    - vip：VIP 有效期内可读（过期即失效）；或已积分购买（永久可读）

    403 携带 tutorial_locked 数据（非 need_auth，避免触发前端全局认证弹窗），
    供阅读页渲染购买/开通会员引导。

    access_mode/points_price 可由调用方传入（已加载教程行时），
    避免详情链路对同一行重复投影查询（PERF）。
    """
    if access_mode is None:
        row = (
            await db.execute(
                select(Tutorial.access_mode, Tutorial.points_price).where(
                    Tutorial.id == tutorial_id,
                    Tutorial.is_published == True,  # noqa: E712
                )
            )
        ).first()
        if not row:
            raise NotFoundError("教程不存在")
        access_mode = row.access_mode
        points_price = row.points_price
    if user is None:
        raise AppException(code=401, message="请先登录后学习教程")
    if access_mode != "vip":
        return
    if is_vip_effective(user):
        return
    if await has_purchased(db, user.id, tutorial_id):
        return

    price = points_price or 0
    hint = f"或使用 {price} 积分购买后永久阅读" if price > 0 else ""
    raise AppException(
        code=403,
        message=f"会员专享教程：认证知识星球成为会员即可阅读{hint}",
        data={"tutorial_locked": True, "points_price": price, "can_purchase": price > 0},
    )


async def purchase_tutorial(db: AsyncSession, user: User, tutorial_id: int) -> dict:
    """积分购买会员教程（购买后永久可读）

    事务语义：先写购买记录（唯一约束兜底并发幂等）再原子扣积分 + 写流水，
    积分不足时抛出 403，购买记录随本事务由 get_db 回滚，不会出现
    "已扣分未解锁"或"已解锁未扣分"的中间态。
    """
    row = (
        await db.execute(
            select(Tutorial.id, Tutorial.title, Tutorial.access_mode, Tutorial.points_price).where(
                Tutorial.id == tutorial_id,
                Tutorial.is_published == True,  # noqa: E712
            )
        )
    ).first()
    if not row:
        raise NotFoundError("教程不存在")
    if row.access_mode != "vip":
        raise AppException(code=400, message="免费教程无需购买")
    cost = row.points_price or 0
    if cost <= 0:
        raise AppException(code=400, message="该教程暂不支持积分购买")
    # VIP 按到期时间即时判定（防过期 VIP 权益滞留），会员无需购买
    if is_vip_effective(user):
        raise AppException(code=400, message="会员可免费阅读会员教程，无需购买")

    # 幂等：已购买直接返回，不重复扣分
    if await has_purchased(db, user.id, tutorial_id):
        return {
            "tutorial_id": tutorial_id,
            "purchased": True,
            "already_purchased": True,
            "points": user.points or 0,
        }

    # savepoint 包裹购买记录写入：并发双击购买时后到请求撞 uq 唯一约束，
    # 仅回滚本段插入（未扣积分），会话不进入失败态
    try:
        async with db.begin_nested():
            db.add(
                TutorialPurchase(
                    user_id=user.id, tutorial_id=tutorial_id, points_paid=cost
                )
            )
            await db.flush()
    except IntegrityError:
        return {
            "tutorial_id": tutorial_id,
            "purchased": True,
            "already_purchased": True,
            "points": user.points or 0,
        }

    # 原子扣减（WHERE points >= cost）：并发请求下防超扣/负余额
    result = await db.execute(
        update(User)
        .where(User.id == user.id, User.points >= cost)
        .values(points=User.points - cost)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise AppException(
            code=403,
            message=f"积分不足：购买本教程需 {cost} 积分，当前余额 {user.points or 0} 积分",
            data={"insufficient_points": True, "need_points": cost},
        )
    # 扣减成功后回读真实余额再写流水（并发充值/扣减场景下内存快照可能陈旧）
    await db.refresh(user)
    record_transaction(
        db,
        user_id=user.id,
        points=-cost,
        balance_after=user.points or 0,
        type=TYPE_CONSUME,
        description=f"购买教程：{row.title}",
    )
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送），
    # 「购买记录 + 扣减余额 + 流水」在本事务内原子提交
    await db.commit()
    return {
        "tutorial_id": tutorial_id,
        "purchased": True,
        "points": user.points or 0,
    }
