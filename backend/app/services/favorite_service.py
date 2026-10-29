"""收藏服务"""

from typing import NamedTuple

from sqlalchemy import select, func, or_, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from loguru import logger

from app.models.favorite import Favorite
from app.core.content_types import CONTENT_TYPES, published_conditions
from app.services.content_availability import unavailable_targets
from app.models.user import User
from app.core.exceptions import AppException, NotFoundError
from app.utils.sanitize import escape_like


class _FavoriteSpec(NamedTuple):
    """一种可收藏内容的描述。

    ``load_columns`` 是 `load_only` 的投影列（只取小字段，避免把 `content`
    MEDIUMTEXT / `inputs` JSON 整行拉进内存再丢弃）；
    ``model`` 为 ``None`` 表示该类型的实体表尚未实现（见 workflow）。
    """

    model: type | None
    load_columns: tuple
    title_attr: str
    cover_attr: str | None
    missing_message: str
    # 目标必须同时满足的条件（发布态等）。取快照时一并校验：
    # 否则知道草稿 ID 就能先收藏，再从收藏列表读到未发布内容的标题与简介
    extra_conditions: tuple = ()


# 收藏目标类型：**派生自** `core.content_types`（唯一注册表，EXT-02）。
#
# 收敛前这里是 5 条 if/elif 分支链 + 一份手写 `TARGET_TYPES = {...}` 集合，两份事实各写各的；
# 更糟的是链尾那条「workflow 占位」是**兜底 return** —— 任何未知类型都会走到它，
# 静默返回「工作流 #123」而不是报错（评论侧的同类缺陷表现为静默删 0 行）。
# 现在未知类型在 `TARGET_TYPES` 校验处就被 400 拦住，占位只对 workflow 生效。
#
# ⚠️ `workflow` 是**预留位**，不是死代码：前后端一致地保留了这个类型（前端收藏页有
#    「工作流」筛选项、小程序同样有），只是实体表尚未实现。删除它属于**产品决策**
#    （要同时改前端筛选项/标签映射），不要当成清理顺手删掉。
_TARGET_SPECS: dict[str, _FavoriteSpec] = {
    module: _FavoriteSpec(
        model=spec.model,
        # 只投影小字段：避免把 `content` MEDIUMTEXT / `inputs` JSON 整行拉进内存再丢弃。
        # ⚠️ 封面列必须由 `cover_attr` **派生**进来，不能另写一份清单：`load_only` 投影掉的列
        # 在异步会话里被 `getattr` 读到就会触发同步懒加载 → MissingGreenlet → 500
        # （2026-10-29 实测「收藏教程」必然 500，而 resource/node/plugin 正常，正是因为只有
        #  教程的 `favorite_cover=True`）。
        load_columns=(
            (
                (
                    spec.model.id,
                    getattr(spec.model, spec.title_attr),
                    spec.model.description,
                )
                + (
                    (getattr(spec.model, spec.cover_attr),)
                    if spec.favorite_cover and spec.cover_attr
                    else ()
                )
            )
            if spec.model is not None
            else ()
        ),
        title_attr=spec.title_attr,
        # 只有教程快照封面（存量数据口径，见注册表 favorite_cover 注释）
        cover_attr=spec.cover_attr if spec.favorite_cover else None,
        missing_message=(
            f"目标{spec.label}不存在" if spec.model is not None else f"{spec.label}尚未实现"
        ),
        # 发布态：草稿不能收藏，也不能靠「知道草稿 ID」从收藏列表读到标题与简介
        extra_conditions=published_conditions(module),
    )
    for module, spec in CONTENT_TYPES.items()
    if spec.favoritable
}

# 对外名字保持不变（两处入口校验与守卫都引用它）
TARGET_TYPES = frozenset(_TARGET_SPECS)


async def _get_target_snapshot(
    db: AsyncSession, target_type: str, target_id: int
) -> tuple[str, str | None, str | None]:
    """根据目标类型与 ID 获取标题/描述/封面快照。

    调用方必须先用 `TARGET_TYPES` 校验（`add_favorite` / 列表回查两处都做了）；
    未知类型现在是 KeyError（程序错误），不再静默落进 workflow 占位分支。
    """
    spec = _TARGET_SPECS[target_type]
    if spec.model is None:
        # workflow 暂未实现实体，使用占位（与收口前的兜底分支逐字一致）
        return f"工作流 #{target_id}", None, None

    obj = (
        await db.execute(
            select(spec.model)
            .options(load_only(*spec.load_columns))
            .where(spec.model.id == target_id, *spec.extra_conditions)
        )
    ).scalar_one_or_none()
    if not obj:
        raise NotFoundError(spec.missing_message)
    return (
        getattr(obj, spec.title_attr),
        obj.description,
        getattr(obj, spec.cover_attr) if spec.cover_attr else None,
    )


async def add_favorite(db: AsyncSession, user: User, target_type: str, target_id: int) -> dict:
    """添加收藏"""
    if target_type not in TARGET_TYPES:
        raise AppException(code=400, message=f"不支持的目标类型: {target_type}")

    # 检查是否已收藏
    existing = (
        await db.execute(
            select(Favorite).where(
                Favorite.user_id == user.id,
                Favorite.target_type == target_type,
                Favorite.target_id == target_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return {"id": existing.id, "message": "已收藏过"}

    # 获取目标快照
    title, description, cover = await _get_target_snapshot(db, target_type, target_id)

    fav = Favorite(
        user_id=user.id,
        target_type=target_type,
        target_id=target_id,
        title=title,
        description=description,
        cover=cover,
    )
    db.add(fav)
    try:
        # SAVEPOINT 包裹：撞唯一约束时仅回滚本条插入。
        # 直接 rollback 会回滚整个请求事务（同请求前置的其他写入静默丢失），
        # 并使 identity map 中 ORM 实例过期，后续懒加载触发 MissingGreenlet。
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # 并发双击收藏：两个请求同时通过 existing 检查后撞唯一约束，
        # 降级为幂等返回"已收藏过"，避免向用户抛 500
        existing = (
            await db.execute(
                select(Favorite.id).where(
                    Favorite.user_id == user.id,
                    Favorite.target_type == target_type,
                    Favorite.target_id == target_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            # 违规并非来自收藏唯一约束（如模型演进新增 FK 约束等），
            # 不可误报"已收藏过"掩盖真实错误
            raise
        return {"id": existing, "message": "已收藏过"}
    logger.info(f"用户 {user.id} 收藏 {target_type}#{target_id}")
    # 显式提交：确保「收藏成功」响应返回前事务已落库（与 remove_favorite 对齐，
    # get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    return {"id": fav.id, "message": "收藏成功"}


async def purge_favorites_for_targets(
    db: AsyncSession, target_type: str, target_ids: list[int]
) -> int:
    """内容被硬删除时，同步清理指向它的收藏记录（P1-7）。

    收藏表只存标题/描述/封面**快照**、没有外键，因此内容删除后不会级联，
    残留行会变成用户「我的收藏」里的**死链**——条目还在，点进去 404，
    且没有任何途径自行清理（用户只能看到永远删不掉的僵尸条目）。

    必须与内容删除在**同一事务**内完成：分开提交会出现「内容已删、收藏还在」
    的中间态，进程崩溃即留下孤儿数据。

    返回被清理的条数（便于测试断言与运维核对）。
    """
    ids = [i for i in (target_ids or []) if i is not None]
    if not ids:
        return 0
    result = await db.execute(
        delete(Favorite)
        .where(Favorite.target_type == target_type, Favorite.target_id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def list_favorites(
    db: AsyncSession,
    user: User,
    page: int = 1,
    page_size: int = 6,
    category: str = None,
    keyword: str = None,
) -> dict:
    """获取收藏列表"""
    conditions = [Favorite.user_id == user.id]
    if category and category != "all":
        conditions.append(Favorite.target_type == category)
    if keyword:
        kw = escape_like(keyword)
        conditions.append(
            or_(
                Favorite.title.contains(kw, escape="\\"),
                Favorite.description.contains(kw, escape="\\"),
            )
        )

    # 直接对主表 count, 避免 subquery 包裹 (PERF)
    total = (await db.execute(select(func.count(Favorite.id)).where(*conditions))).scalar()
    query = (
        select(Favorite).where(*conditions)
        # 必须带 id 收尾：created_at 是秒级 DATETIME，同秒创建的多条在翻页时会重复/漏项
        .order_by(Favorite.created_at.desc(), Favorite.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(query)).scalars().all()

    # 目标可能已被删除或取消发布：收藏是用户主动保存的资产，不静默隐藏，
    # 而是标记 available=False 让前端显示「已下架」并保留取消收藏入口
    gone = await unavailable_targets(db, [(f.target_type, f.target_id) for f in rows])
    items = [
        {
            "id": f.id,
            "target_type": f.target_type,
            "target_id": f.target_id,
            "title": f.title,
            "description": f.description,
            "cover": f.cover,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "available": (f.target_type, f.target_id) not in gone,
        }
        for f in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def remove_favorite(db: AsyncSession, user: User, favorite_id: int) -> None:
    """取消收藏"""
    fav = (
        await db.execute(
            select(Favorite).where(Favorite.id == favorite_id, Favorite.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not fav:
        raise NotFoundError("收藏记录不存在")
    await db.delete(fav)
    await db.commit()


async def check_favorite(db: AsyncSession, user: User, target_type: str, target_id: int) -> dict:
    """检查是否已收藏"""
    if target_type not in TARGET_TYPES:
        raise AppException(code=400, message=f"不支持的目标类型: {target_type}")
    fav = (
        await db.execute(
            select(Favorite.id).where(
                Favorite.user_id == user.id,
                Favorite.target_type == target_type,
                Favorite.target_id == target_id,
            )
        )
    ).scalar_one_or_none()
    return {"collected": fav is not None, "favorite_id": fav}
