"""学习记录服务"""

from datetime import datetime, timezone
from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.learning import LearningRecord
from app.models.tutorial import Tutorial
from app.models.user import User
from app.core.exceptions import NotFoundError
from app.services.content_availability import unavailable_targets
from app.utils.sanitize import escape_like


def _clamp_progress(progress: int | None) -> int:
    """进度钳制到 [0, 100]，防路由层校验缺失时负数/超大值入库"""
    if progress is None:
        return 0
    return max(0, min(progress, 100))


async def purge_learning_records_for_tutorials(
    db: AsyncSession, tutorial_ids: list[int]
) -> int:
    """教程被硬删除时，同步清理指向它的学习记录（P1-7）。

    学习记录存的是教程标题/封面**快照**、无外键，教程删除后不会级联，
    残留在「我的学习」「继续学习」里就是点开即 404 的死条目。

    与教程删除同事务提交，避免出现「教程已删、记录还在」的中间态。
    返回被清理的条数。
    """
    ids = [i for i in (tutorial_ids or []) if i is not None]
    if not ids:
        return 0
    result = await db.execute(
        delete(LearningRecord)
        .where(LearningRecord.tutorial_id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def upsert_learning_record(
    db: AsyncSession,
    user: User,
    tutorial_id: int,
    progress: int = None,
    current_chapter: str = None,
) -> LearningRecord:
    """新增或更新学习记录（被 track 接口调用）"""
    # 校验教程存在（仅取必要字段，避免加载整行 content）
    tutorial = (
        await db.execute(
            select(Tutorial.id, Tutorial.title, Tutorial.cover_image).where(
                Tutorial.id == tutorial_id
            )
        )
    ).one_or_none()
    if not tutorial:
        raise NotFoundError("教程不存在")

    record = (
        await db.execute(
            select(LearningRecord).where(
                LearningRecord.user_id == user.id,
                LearningRecord.tutorial_id == tutorial_id,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if record is None:
        record = LearningRecord(
            user_id=user.id,
            tutorial_id=tutorial_id,
            title=tutorial.title,
            cover=tutorial.cover_image,
            current_chapter=current_chapter,
            progress=_clamp_progress(progress),
            status="in_progress",
            last_study_at=now,
        )
        db.add(record)
        try:
            # SAVEPOINT：并发首报（双开页面等）双方都查不到已有记录，
            # 一方 INSERT 撞 uq_user_tutorial 唯一约束时仅回滚本条，
            # 幂等降级为更新已存在记录，避免裸 500
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            record = (
                await db.execute(
                    select(LearningRecord).where(
                        LearningRecord.user_id == user.id,
                        LearningRecord.tutorial_id == tutorial_id,
                    )
                )
            ).scalar_one()
            if current_chapter:
                record.current_chapter = current_chapter
            record.last_study_at = now
            await db.flush()
        return record

    # 同步标题/封面（教程可能被更新）
    record.title = tutorial.title
    record.cover = tutorial.cover_image
    if progress is not None:
        # 与插入路径同口径：先钳制到 [0,100] 再取历史最大值（进度只增不减）
        record.progress = max(record.progress, _clamp_progress(progress))
        if record.progress >= 100:
            record.status = "completed"
    if current_chapter:
        record.current_chapter = current_chapter
    record.last_study_at = now
    await db.flush()
    return record


async def list_learning_records(
    db: AsyncSession,
    user: User,
    page: int = 1,
    page_size: int = 6,
    status: str = None,
    keyword: str = None,
) -> dict:
    """学习记录列表"""
    conditions = [LearningRecord.user_id == user.id]
    if status and status != "all":
        conditions.append(LearningRecord.status == status)
    if keyword:
        conditions.append(
            LearningRecord.title.contains(escape_like(keyword), escape="\\")
        )

    # 与「收藏」同口径（见 `content_availability` 模块文档）：学习记录**保留历史**，
    # 但对「教程已删除 / 已取消发布」的条目标 `available=False`，前端据此标「已下架」
    # 并禁用跳转按钮 —— 用户点进去只会拿到「教程不存在」。
    #
    # 这里**不**在查询层过滤（那是下载历史的做法）：学习记录是用户自己的学习轨迹，
    # 一次下架就让 10 条记录凭空消失，比看到「已下架」更让人困惑。
    total = (await db.execute(select(func.count(LearningRecord.id)).where(*conditions))).scalar()
    # cover 同上：以教程实时封面为准，记录里的快照只兜底
    query = (
        select(LearningRecord, Tutorial.cover_image)
        .outerjoin(Tutorial, LearningRecord.tutorial_id == Tutorial.id)
        .where(*conditions)
        # 同上：同秒更新的记录需要唯一列兜底，否则翻页不稳定
        .order_by(LearningRecord.last_study_at.desc(), LearningRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(query)).all()

    # 一次 IN 查询批量判定（按类型分组），不逐条查
    gone = await unavailable_targets(db, [("tutorial", r.tutorial_id) for r, _ in rows])

    items = [
        {
            "id": r.tutorial_id,  # 前端用 tutorial_id 跳转
            "tutorial_id": r.tutorial_id,
            "title": r.title,
            "chapter": r.current_chapter,
            "progress": r.progress,
            "status": r.status,
            "last_study_at": r.last_study_at.isoformat() if r.last_study_at else None,
            "cover": tutorial_cover or r.cover,
            "available": ("tutorial", r.tutorial_id) not in gone,
        }
        for r, tutorial_cover in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def list_continue_learning(
    db: AsyncSession,
    user: User,
    limit: int = 2,
) -> list:
    """继续学习（最近在学的 N 条）

    ⚠️ 排序规则（2026-10-20 修）：**未完成的优先，不足 N 条用最近学过的补**。
    此前只查 `status == "in_progress"`，导致「学完了就永久消失」：
    而 `status` 是从「阅读位置」推导的（前端按当前章节在目录里的位置上报 progress，
    点最后一节就等于 100%），用户只是翻到后面看一眼也会被判完成，
    之后个人中心「继续学习」里再也看不到这篇 —— 用户视角就是「我明明看了却没记录」。

    现在改成一条 ORDER BY：`in_progress` 排前面（CASE 0/1），组内都按
    `last_study_at` 倒序。这样「接着学」的语义保留，刚看过的也一定出现，
    返回里带 `status` 供前端把「已学完」标出来。
    """
    # 教程被删除/取消发布时不再出现在「继续学习」里（点进去只会 404）
    #
    # ⚠️ cover 以 `Tutorial.cover_image` 为准，`LearningRecord.cover` 只作兜底：
    # 记录里的 cover 是「创建/更新学习记录那一刻」的快照，用户在后台**后来**才设封面时，
    # 老记录的 cover 仍是空的 —— 表现为「后台明明配了首图，个人中心却不显示」。
    query = (
        select(LearningRecord, Tutorial.cover_image)
        .join(Tutorial, LearningRecord.tutorial_id == Tutorial.id)
        .where(
            LearningRecord.user_id == user.id,
            Tutorial.is_published.is_(True),
        )
        .order_by(
            # 0 = 进行中（排前），1 = 已完成（补位）
            case((LearningRecord.status == "in_progress", 0), else_=1),
            # 同秒更新的记录需要唯一列兜底，否则翻页不稳定
            LearningRecord.last_study_at.desc(),
            LearningRecord.id.desc(),
        )
        .limit(limit)
    )
    rows = (await db.execute(query)).all()
    return [
        {
            "id": r.tutorial_id,
            "tutorial_id": r.tutorial_id,
            "title": r.title,
            "chapter": r.current_chapter,
            "progress": r.progress,
            "status": r.status,
            "cover": tutorial_cover or r.cover,
            "last_study_at": r.last_study_at.isoformat() if r.last_study_at else None,
        }
        for r, tutorial_cover in rows
    ]


async def get_learning_stats(db: AsyncSession, user: User) -> dict:
    """学习记录统计

    单条 GROUP BY 查询替代 3 次串行 COUNT，减少数据库往返。

    口径与 `list_learning_records` 一致：**统计全部记录**（含指向已下架教程的），
    因为列表也保留它们并标「已下架」。若这里偷偷过滤，就会出现
    「共 1 门」而列表有 11 条的自相矛盾。
    """
    counts = {"in_progress": 0, "completed": 0}
    rows = (
        await db.execute(
            select(LearningRecord.status, func.count(LearningRecord.id))
            .where(LearningRecord.user_id == user.id)
            .group_by(LearningRecord.status)
        )
    ).all()
    for status, count in rows:
        counts[status] = count
    total = sum(counts.values())
    return {
        "total": total,
        "in_progress": counts.get("in_progress", 0),
        "completed": counts.get("completed", 0),
    }
