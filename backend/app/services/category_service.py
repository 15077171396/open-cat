"""分类管理服务"""

import json
import random

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_types import CONTENT_TYPES
from app.core.exceptions import AppException, NotFoundError
from app.models.category import Category
# 仅 `list_categories_with_tutorial_count` 需要（教程分类计数，不是通用映射）
from app.models.tutorial import Tutorial
from app.redis import log_redis_degraded, redis_client


# 模块 → 内容表 + 分类字段 的映射。
# **派生自** `core.content_types`（唯一注册表，EXT-02）：此前这里是一份手抄清单，
# 与评论/收藏/通知/审计各写一份，新增内容类型时漏掉一处就表现为
# 「分类页点新增必然 422」或「该类内容无法按分类筛选」。
# 预留类型（model 为 None）不进这张表 —— 没有实体表就没有分类可查。
MODULE_TABLE_MAP = {
    module: (spec.model, spec.category_attr)
    for module, spec in CONTENT_TYPES.items()
    if spec.model is not None
}

# 公开分类缓存 TTL（秒）+ 抖动上限：分类近静态但公开接口高频访问，
# 抖动防同批 key 同时过期；管理端写操作主动失效
_CACHE_TTL = 120
_CACHE_TTL_JITTER = 30


async def list_categories(db: AsyncSession, module: str) -> list[Category]:
    """获取指定模块的分类列表"""
    result = await db.execute(
        select(Category)
        .where(Category.module == module)
        .order_by(Category.sort_order.asc(), Category.id.asc())
    )
    return list(result.scalars().all())


async def is_known_category(db: AsyncSession, module: str, value: str | None) -> bool:
    """``value`` 是否是 ``categories`` 表里登记过的分类名（公开列表的入参校验）。

    为什么需要它（第二轮复审 C-05）：公开列表的缓存键包含用户传来的分类参数，
    而参数此前**没有任何约束** —— 任意随机串都是一个新 key，每个 key 存一整页 JSON，
    TTL 45~60s。配合 Redis 的 ``noeviction`` 策略，刷 key 能把实例写满，
    进而让限流 fail-open、浏览量退回每次请求写库（一个疏漏打掉三条保护）。

    收敛方式：**只有登记过的分类才进缓存**。未知值照旧参与 WHERE（结果是空列表，
    语义不变），只是不写缓存 —— key 基数因此被分类表大小钉死。

    读不到分类表（DB 与 Redis 同时不可用）时返回 True：宁可多缓存一次，
    也不要因为一次抖动把正常用户的分类页全判成非法。
    """
    if not value:
        return True
    try:
        names = {c["name"] for c in await get_cached_categories(db, module)}
    except Exception as exc:  # noqa: BLE001 分类表读不到时保守放行（只影响是否缓存）
        log_redis_degraded(
            "category.is_known",
            f"读取分类表失败，本次跳过分类白名单校验（{type(exc).__name__}: {exc}）",
        )
        return True
    return value in names


async def get_cached_categories(db: AsyncSession, module: str) -> list[dict]:
    """获取指定模块的分类列表（公开接口用，带 Redis 缓存，Redis 故障降级直查 DB）"""
    cache_key = f"categories:{module}"
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        log_redis_degraded("category._cache_get", "Redis 不可用，分类缓存降级直查 DB")

    rows = await list_categories(db, module)
    data = [{"id": c.id, "name": c.name, "sort_order": c.sort_order} for c in rows]
    try:
        await redis_client.set(cache_key, json.dumps(data, ensure_ascii=False), ex=_CACHE_TTL + random.randint(0, _CACHE_TTL_JITTER))
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("category_service.get_cached_categories", exc)
    return data


async def invalidate_category_cache(module: str) -> None:
    """分类变更后失效公开缓存（失败仅记日志：TTL 兜底最长 2 分钟陈旧）"""
    try:
        await redis_client.delete(f"categories:{module}")
    except Exception:
        log_redis_degraded("category.invalidate", f"分类缓存失效失败（Redis 不可用）: {module}")


async def get_module_overview(db: AsyncSession, module: str) -> dict:
    """分类管理页数据：分类 + 各分类内容数 + 「内容在用但分类表未登记」的分类。

    刻意**不走 Redis**（对比 `get_cached_categories`）：内容数量随内容增删变化，
    而分类缓存的失效只由分类写操作触发 —— 拿缓存数字当「0 条 → 可以删」的依据会误导运营
    （删除接口仍会拦，但运营会先看到假数字）。

    孤儿分类（内容里出现、categories 表没有的取值）是真实存在的问题：运营改名、
    第三方导入、或早期硬编码分类都会产生，前台按这些值筛选时会筛不到东西。
    这里显式列出来，让运营能补登记或改名。
    """
    table_cls, field_name = MODULE_TABLE_MAP[module]
    field = getattr(table_cls, field_name)
    # 单次 GROUP BY 取全部计数，避免「每个分类一条 COUNT」的 N+1
    count_rows = (await db.execute(select(field, func.count()).group_by(field))).all()
    counts: dict[str, int] = {name: count for name, count in count_rows if name}
    total_content = sum(counts.values())

    categories = await list_categories(db, module)
    registered = {c.name for c in categories}
    items = [
        {
            "id": c.id,
            "name": c.name,
            "sort_order": c.sort_order,
            "content_count": counts.get(c.name, 0),
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in categories
    ]
    orphans = sorted(
        (
            {"name": name, "content_count": count}
            for name, count in counts.items()
            if name not in registered
        ),
        key=lambda o: (-o["content_count"], o["name"]),
    )
    return {
        "module": module,
        "items": items,
        "orphans": orphans,
        "total_content": total_content,
    }


async def reorder_categories(db: AsyncSession, module: str, ids: list[int]) -> list[dict]:
    """按给定 id 顺序重排某模块的分类，sort_order 从 1 起连续赋值。

    ``ids`` 必须恰好覆盖该模块的全部分类：缺项/多项/重复一律拒绝 —— 整组一次性
    提交，不留「改了一半」的中间态（分多次 PUT 交换的话，中途失败就会产生并列排序）。
    """
    categories = await list_categories(db, module)
    if sorted(ids) != sorted(c.id for c in categories):
        raise AppException(code=400, message="分类列表已变化，请刷新后重试")

    by_id = {c.id: c for c in categories}
    for index, category_id in enumerate(ids):
        by_id[category_id].sort_order = index + 1
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    await invalidate_category_cache(module)
    return [{"id": category_id, "sort_order": index + 1} for index, category_id in enumerate(ids)]


async def list_categories_with_tutorial_count(db: AsyncSession) -> list[dict]:
    """教程分类 + 各分类已发布教程数（首页「热门学习方向」卡片使用）"""
    result = await db.execute(
        select(
            Category,
            func.count(Tutorial.id),
        )
        .outerjoin(Tutorial, (Tutorial.category == Category.name) & Tutorial.is_published)
        .where(Category.module == "tutorial")
        .group_by(Category.id)
        .order_by(Category.sort_order.asc(), Category.id.asc())
    )
    return [
        {"id": c.id, "name": c.name, "sort_order": c.sort_order, "tutorial_count": count}
        for c, count in result.all()
    ]


async def create_category(db: AsyncSession, module: str, name: str) -> Category:
    """创建分类"""
    # 检查重复
    existing = await db.execute(
        select(Category).where(Category.module == module, Category.name == name)
    )
    if existing.scalar_one_or_none():
        raise AppException(code=400, message=f"分类 '{name}' 已存在")

    # 计算排序值(放到最后)
    max_order = await db.execute(
        select(func.coalesce(func.max(Category.sort_order), 0))
        .where(Category.module == module)
    )
    sort_order = (max_order.scalar() or 0) + 1

    category = Category(module=module, name=name, sort_order=sort_order)
    db.add(category)
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    await db.refresh(category)
    await invalidate_category_cache(module)
    return category


async def update_category(db: AsyncSession, category_id: int, name: str) -> Category:
    """更新分类名"""
    category = await _get_category(db, category_id)

    # 检查新名称是否重复
    if name != category.name:
        existing = await db.execute(
            select(Category).where(
                Category.module == category.module,
                Category.name == name,
                Category.id != category_id,
            )
        )
        if existing.scalar_one_or_none():
            raise AppException(code=400, message=f"分类 '{name}' 已存在")

        old_name = category.name
        # 同步更新所有内容的分类字段
        await _sync_content_category(db, category.module, old_name, name)

    category.name = name
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    await db.refresh(category)
    await invalidate_category_cache(category.module)
    return category


async def delete_category(db: AsyncSession, category_id: int) -> None:
    """删除分类(若该分类下有内容则拒绝)"""
    category = await _get_category(db, category_id)

    # 检查是否有关联内容
    count = await _count_content_by_category(db, category.module, category.name)
    if count > 0:
        raise AppException(code=400, message=f"该分类下有 {count} 条内容,无法删除")

    await db.delete(category)
    await db.commit()
    await invalidate_category_cache(category.module)


async def _get_category(db: AsyncSession, category_id: int) -> Category:
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise NotFoundError("分类不存在")
    return category


async def _count_content_by_category(db: AsyncSession, module: str, name: str) -> int:
    """统计某分类下的内容数量"""
    table_cls, field_name = MODULE_TABLE_MAP[module]
    field = getattr(table_cls, field_name)
    result = await db.execute(
        select(func.count()).where(field == name)
    )
    return result.scalar() or 0


async def _sync_content_category(db: AsyncSession, module: str, old_name: str, new_name: str) -> None:
    """同步更新内容表的分类字段（单条 UPDATE，避免逐行 setattr）"""
    table_cls, field_name = MODULE_TABLE_MAP[module]
    field = getattr(table_cls, field_name)
    await db.execute(
        update(table_cls).where(field == old_name).values(**{field_name: new_name})
    )
