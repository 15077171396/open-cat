"""内容评论服务。设计要点：
1. 评论必须过敏感词（screen_text + log_hit 留痕），审核异常不抛出但拒绝类处置必须拦下；
2. 软删除（deleted_at），删顶层评论连带软删其回复；
3. 只做两级回复（回复的回复挂到同一顶层父评论下）；
4. 配图只接受本站上传产出的地址（否则评论区变成任意图床）；
5. 内容硬删除必须连带清理评论（评论表无外键级联，否则留孤儿 UGC）；
6. 多态目标 (target_type, target_id)：新增可评论类型需登记 5 处，
   见 _TARGET_SPECS 注释与 tests/test_comment_target_registry.py。
"""

from __future__ import annotations

import datetime as dt
from typing import NamedTuple

from loguru import logger
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_types import CONTENT_TYPES, published_conditions
from app.core.exceptions import AppException, NotFoundError
from app.models.comment import (
    COMMENT_MAX_IMAGES,
    COMMENT_MAX_LENGTH,
    COMMENT_TARGET_TUTORIAL,
    COMMENT_TARGET_TYPES,
    MAX_COMMENTS_PER_USER_PER_DAY,
    MAX_REPLIES_PER_COMMENT,
    ContentComment,
)
from app.models.user import User
from app.models.message import Message
from app.services import moderation_service
from app.utils.image_urls import validate_uploaded_image_urls
from app.utils.sanitize import escape_like, sanitize_plain_text

# 评论正文长度上限单源于 models.comment 定义，避免与 DB 列宽两份事实来源不一致


class _TargetSpec(NamedTuple):
    """一种可评论内容的描述：title_column 供后台列表显示所属内容，
    extra_conditions 是存在性校验的附加条件（如教程必须已发布）"""

    model: type
    title_column: object
    extra_conditions: tuple
    missing_message: str


# 可评论内容类型：**派生自** `core.content_types`（唯一注册表，EXT-02），
# 不再手抄第二份清单。新增一种可评论类型只需在注册表里把 `commentable=True`
# 并同步 `models.comment.COMMENT_TARGET_TYPES`（下面有导入期断言锁死两者相等）。
#
# 存在性校验的附加条件是「该类型必须已发布」——与前台详情页的可见性口径一致，
# 由注册表的 `published_attr` 决定（无发布态的类型会得到空元组）。
_TARGET_SPECS: dict[str, _TargetSpec] = {
    module: _TargetSpec(
        model=spec.model,
        title_column=getattr(spec.model, spec.title_attr),
        extra_conditions=published_conditions(module),
        missing_message=f"{spec.label}不存在或未发布",
    )
    for module, spec in CONTENT_TYPES.items()
    if spec.commentable and spec.model is not None
}

if set(_TARGET_SPECS) != set(COMMENT_TARGET_TYPES):  # pragma: no cover - 只在漏登记时触发
    raise RuntimeError(
        "评论内容类型注册表与 COMMENT_TARGET_TYPES 不一致："
        f"注册表 {sorted(_TARGET_SPECS)} vs 常量 {sorted(COMMENT_TARGET_TYPES)}。"
        "可评论类型由 core.content_types 的 commentable 标记决定，"
        "新增时两处必须同时登记（漏了会让该类型要么无法评论、要么评论成孤儿 UGC）。"
    )


def registered_comment_targets() -> tuple[str, ...]:
    """当前可评论的内容类型。路由取值说明与守卫都用它，不要再手写清单。"""
    return tuple(_TARGET_SPECS)


def _target_spec(target_type: str) -> _TargetSpec:
    """目标类型 → 描述；列表、发布、回复、后台展示、清理全部由它派生。"""
    spec = _TARGET_SPECS.get(target_type)
    if spec is None:
        raise AppException(code=400, message="不支持的内容类型")
    return spec


def _serialize(comment: ContentComment, author: User | None, *, can_manage: bool = False) -> dict:
    """评论输出；can_manage 控制是否暴露内部字段（是否已删除），前台一律 False"""
    data = {
        "id": comment.id,
        "target_type": comment.target_type,
        "target_id": comment.target_id,
        "user_id": comment.user_id,
        "parent_id": comment.parent_id,
        "author_nickname": (author.nickname if author else None) or "匿名用户",
        "author_avatar": author.avatar_url if author else None,
        "content": comment.content,
        # 统一成列表：NULL 与 [] 对前端是同一件事（没有配图）
        "images": list(comment.images or []),
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }
    if can_manage:
        data["deleted_at"] = comment.deleted_at.isoformat() if comment.deleted_at else None
        data["is_deleted"] = comment.deleted_at is not None
        data["updated_at"] = comment.updated_at.isoformat() if comment.updated_at else None
        if author is not None:
            data["author_account"] = author.account
    return data


def _validate_images(images) -> list[str]:
    """校验配图 URL 列表（数量上限 + 必须本站 COS/CDN 域名）。

    规则与反馈配图共用 `utils/image_urls.validate_uploaded_image_urls`，
    这里只负责把评论的数量上限传进去 —— 两处各写一份迟早漂移成行为差异。
    """
    return validate_uploaded_image_urls(images, max_count=COMMENT_MAX_IMAGES)


#: 内容类型 → 前端详情页路径。新增可评论类型时**必须同步这里**，
#: 否则通知点进去是死链（比没有链接更糟：用户点了才发现打不开）。
_DETAIL_PATHS = {
    "tutorial": "/tutorials/{}",
    "node": "/nodes/{}",
    "resource": "/resources/{}",
    "plugin": "/plugins/{}",
    "tool": "/tools/{}",
}


def _detail_path(target_type: str, target_id: int) -> str | None:
    template = _DETAIL_PATHS.get(target_type)
    return template.format(target_id) if template else None


async def _notify_comment_reply(
    db: AsyncSession,
    *,
    receiver_id: int,
    sender: User,
    target_type: str,
    target_id: int,
    content: str,
    images: list[str],
) -> None:
    """给被回复者写一条站内消息（只 add 不 commit，与评论同事务）。

    收件人已注销时静默跳过：给软删用户写消息既无意义，还会污染未读统计。
    自己回自己也不通知（调用方已挡，这里再兜一层 —— 这是最容易在重构中丢掉的判断）。
    """
    if receiver_id == sender.id:
        return
    alive = (
        await db.execute(
            select(User.id).where(User.id == receiver_id, User.deleted_at.is_(None))
        )
    ).first()
    if alive is None:
        return

    # 通知里只放预览：正文可能很长，消息列表也不该变成第二个评论区
    preview = (content or "").strip()
    if not preview:
        preview = "（图片）" if images else ""
    elif len(preview) > 100:
        preview = preview[:100] + "…"

    sender_name = (sender.nickname or "").strip() or sender.account or "有用户"
    db.add(
        Message(
            user_id=receiver_id,
            sender_id=sender.id,
            type="comment",
            title=f"{sender_name} 回复了你的评论"[:200],
            content=preview,
            avatar=sender.avatar_url,
            link=_detail_path(target_type, target_id),
            is_read=False,
        )
    )


async def list_comments(
    db: AsyncSession,
    target_type: str,
    target_id: int,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """某内容的评论列表（公开；只返回未删除的顶层评论，回复内嵌在各自父评论下）。

    分页只对顶层评论生效（否则一条热评的几十条回复会把后面的顶层评论挤出当前页）；
    作者昵称/头像对当页所有评论一次性回查（N+1 会拖垮列表接口）。
    ⚠️ 读侧必须过与写侧相同的目标可见性判定：内容下架后整条讨论仍可被匿名分页拉走
    （评论含用户可识别信息）；不可见时返回空分页而非 404，既关掉泄漏面也不让已打开页面白屏。
    """
    spec = _target_spec(target_type)
    visible = (
        await db.execute(
            select(spec.model.id).where(spec.model.id == target_id, *spec.extra_conditions)
        )
    ).first()
    if visible is None:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    base = [
        ContentComment.target_type == target_type,
        ContentComment.target_id == target_id,
        ContentComment.deleted_at.is_(None),
    ]
    top_conds = base + [ContentComment.parent_id.is_(None)]

    total = (
        await db.execute(select(func.count(ContentComment.id)).where(*top_conds))
    ).scalar() or 0
    if not total:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    rows = (
        await db.execute(
            select(ContentComment)
            .where(*top_conds)
            # 必须带 id.desc() 兜底：created_at 是秒级 DATETIME，同一秒发布的两条评论
            # 无唯一列收尾时翻页会重复/漏项
            .order_by(ContentComment.created_at.desc(), ContentComment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    parent_ids = [c.id for c in rows]
    replies: list[ContentComment] = []
    reply_counts: dict[int, int] = {}
    if parent_ids:
        # 回复总数用 GROUP BY 取真实值（不受单条返回上限影响）
        reply_counts = {
            pid: cnt
            for pid, cnt in (
                await db.execute(
                    select(ContentComment.parent_id, func.count(ContentComment.id))
                    .where(*base, ContentComment.parent_id.in_(parent_ids))
                    .group_by(ContentComment.parent_id)
                )
            ).all()
        }
        # 回复数 ≤ 展示上限的父评论：一条 IN 查询全部取回（读量有硬上界 page_size × N）；
        # 只有「热评」（回复数 > N）才各自一条带 LIMIT 的查询。排序一律带 id 兜底，
        # 否则 created_at 同秒并列时 LIMIT 截断集合不确定，翻页会重复/漏项
        small = [
            pid for pid in parent_ids
            if reply_counts.get(pid, 0) <= MAX_REPLIES_PER_COMMENT
        ]
        if small:
            replies.extend(
                (
                    await db.execute(
                        select(ContentComment)
                        .where(*base, ContentComment.parent_id.in_(small))
                        .order_by(
                            ContentComment.parent_id,
                            ContentComment.created_at.asc(),
                            ContentComment.id.asc(),
                        )
                    )
                ).scalars().all()
            )
        for pid in parent_ids:
            if reply_counts.get(pid, 0) <= MAX_REPLIES_PER_COMMENT:
                continue
            replies.extend(
                (
                    await db.execute(
                        select(ContentComment)
                        .where(*base, ContentComment.parent_id == pid)
                        .order_by(ContentComment.created_at.asc(), ContentComment.id.asc())
                        .limit(MAX_REPLIES_PER_COMMENT)
                    )
                ).scalars().all()
            )

    # 作者一次性回查（顶层 + 回复合并去重）
    author_ids = {c.user_id for c in rows} | {r.user_id for r in replies}
    authors: dict[int, User] = {}
    if author_ids:
        users = (
            await db.execute(select(User).where(User.id.in_(author_ids)))
        ).scalars().all()
        authors = {u.id: u for u in users}

    grouped: dict[int, list[dict]] = {}
    for reply in replies:
        bucket = grouped.setdefault(reply.parent_id, [])
        # 这道 Python 侧截断**不是冗余**：`reply_counts`（GROUP BY）与取回复是两条语句，
        # 中间可能有并发插入把某个「小集合」父评论顶过 N 条；SQL 侧的 LIMIT 只覆盖热评分支
        if len(bucket) < MAX_REPLIES_PER_COMMENT:
            bucket.append(_serialize(reply, authors.get(reply.user_id)))

    items = []
    for comment in rows:
        item = _serialize(comment, authors.get(comment.user_id))
        item["replies"] = grouped.get(comment.id, [])
        item["reply_count"] = reply_counts.get(comment.id, 0)
        items.append(item)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def create_comment(
    db: AsyncSession,
    user: User,
    target_type: str,
    target_id: int,
    content: str,
    parent_id: int | None = None,
    images: list[str] | None = None,
) -> dict:
    """发布评论或回复（需登录）。

    校验顺序刻意是「长度 → 图片 → 空值 → 频次 → 目标/父评论存在性 → 敏感词留痕」：廉价校验在前。
    敏感词留痕排在频次/存在性之后：配额数的是业务行数，被拒提交不产生行，若留痕在前，
    固定命中词库的正文循环提交即可日增数十万留痕行、淹掉信噪比（另一半由 log_hit 的每用户留痕上界兜住）。
    允许只发图不写字：截图提问很常见，强制写字只会让人随便敲几个字凑数。
    """
    spec = _target_spec(target_type)

    # 被回复的那条评论的作者（通知对象）。必须在 parent_id 被提升到顶层**之前**记下来：
    # 回复「某条回复」时，被回复的是那层回复的作者，而不是顶层评论的作者。
    reply_to_user_id: int | None = None

    content = (content or "").strip()
    if len(content) > COMMENT_MAX_LENGTH:
        raise AppException(code=400, message=f"评论最长 {COMMENT_MAX_LENGTH} 个字符")
    # 纯文本字段：剥离全部 HTML 标签防存储型 XSS（与昵称/简介同口径）
    content = sanitize_plain_text(content).strip() if content else ""

    images = _validate_images(images)

    if not content and not images:
        raise AppException(code=400, message="评论内容不能为空")

    await _check_daily_quota(db, user.id)

    exists = (
        await db.execute(
            select(spec.model.id).where(spec.model.id == target_id, *spec.extra_conditions)
        )
    ).first()
    if exists is None:
        raise NotFoundError(spec.missing_message)

    if parent_id is not None:
        parent = (
            await db.execute(
                select(ContentComment).where(
                    ContentComment.id == parent_id,
                    ContentComment.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise NotFoundError("要回复的评论不存在或已删除")
        # 跨内容回复必须拦下：否则回复会挂到「别的内容」的评论树下，界面上永远看不到
        if parent.target_type != target_type or parent.target_id != target_id:
            raise AppException(code=400, message="回复的评论不属于当前内容")
        # 通知对象在提升前记录（见函数开头注释）
        reply_to_user_id = parent.user_id
        # 只做两级：回复「某条回复」时，挂到它所属的顶层评论下。
        # 前端也按同一规则展示，两边口径必须一致，否则回复会「找不到位置」。
        if parent.parent_id is not None:
            parent_id = parent.parent_id

    # 敏感词：命中即留痕，reject 处置直接拒绝提交（无正文时无需过筛）；位置理由见函数 docstring
    if content:
        screen = moderation_service.screen_text(content)
        if screen.hits:
            await moderation_service.log_hit(
                db, content_type="comment", user_id=user.id,
                text=content, hits=screen.hits, action=screen.action,
            )
            if not screen.passed:
                await db.commit()  # 留痕必须留下，本体不入库
                raise AppException(code=400, message="评论包含不当信息，请修改后重新发布")
            content = screen.text or content

    comment = ContentComment(
        target_type=target_type,
        target_id=target_id,
        user_id=user.id,
        parent_id=parent_id,
        content=content,
        images=images or None,
    )
    db.add(comment)
    # 回复通知：与评论同事务 —— 评论没落库就不该有通知（回滚时通知一起消失）
    if reply_to_user_id is not None:
        await _notify_comment_reply(
            db,
            receiver_id=reply_to_user_id,
            sender=user,
            target_type=target_type,
            target_id=target_id,
            content=content,
            images=images,
        )
    await db.commit()
    await db.refresh(comment)
    logger.info(
        f"用户 {user.id} 在 {target_type} {target_id} 发布"
        f"{'回复' if parent_id else '评论'} {comment.id}"
        f"（配图 {len(images)} 张）"
    )
    return _serialize(comment, user)


async def _check_daily_quota(db: AsyncSession, user_id: int) -> None:
    """单用户每日评论上限（防灌水），按自然日统计（排查与定时清理口径直观）。
    日期基准刻意取 naive UTC：MySQL DATETIME 不带时区、全库按 UTC 写入/解读，
    用 aware datetime 比较会依赖驱动丢 tzinfo 的隐式行为，时区不一致时整体错位。"""
    today_start = dt.datetime.now(dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    count = (
        await db.execute(
            select(func.count(ContentComment.id)).where(
                ContentComment.user_id == user_id,
                ContentComment.created_at >= today_start,
            )
        )
    ).scalar() or 0
    if count >= MAX_COMMENTS_PER_USER_PER_DAY:
        raise AppException(
            code=429, message=f"今日评论已达上限（{MAX_COMMENTS_PER_USER_PER_DAY} 条），请明天再来"
        )


async def delete_comment(
    db: AsyncSession, comment_id: int, user_id: int, *, is_admin: bool = False
) -> dict:
    """删除评论（软删除）。管理员可删任何评论；普通用户只能删自己的（越权返回 403 而非 404）。
    删顶层评论连带软删其回复（语义等同删除整条讨论），管理员可用 restore_comment 恢复。"""
    comment = await _get_comment(db, comment_id)
    if not is_admin and comment.user_id != user_id:
        raise AppException(code=403, message="只能删除自己发布的评论")
    if comment.deleted_at is None:
        now = dt.datetime.now(dt.timezone.utc)
        comment.deleted_at = now
        if comment.parent_id is None:
            # 单条 UPDATE 批量软删，不逐条 db.delete()（回复可能几十条）
            await db.execute(
                sa_update(ContentComment)
                .where(
                    ContentComment.parent_id == comment.id,
                    ContentComment.deleted_at.is_(None),
                )
                .values(deleted_at=now)
            )
        await db.commit()
    return {"id": comment.id, "deleted": True}


async def _get_comment(db: AsyncSession, comment_id: int) -> ContentComment:
    comment = (
        await db.execute(select(ContentComment).where(ContentComment.id == comment_id))
    ).scalar_one_or_none()
    if comment is None:
        raise NotFoundError("评论不存在")
    return comment


# 管理后台


async def admin_list_comments(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    include_deleted: bool = False,
    start_at: dt.datetime | None = None,
    end_at: dt.datetime | None = None,
    deleted_only: bool = False,
) -> dict:
    """评论列表（后台）：可按内容类型/ID 筛选、按正文关键词搜索、查看已删除项

    `start_at` / `end_at` 按**发布时间**闭区间，且必须是 **naive UTC**
    （路由层用 `utils.time.parse_iso_to_naive_utc` 从前端 ISO 串换算，别在这里猜时区）。
    引入它的是审核场景：「昨天/本周新增了哪些评论」是最常见的排查入口，
    而此前只能靠翻页按时间倒序找。
    """
    conds = []
    if deleted_only:
        # 回收站视图只看已删除项；与 include_deleted 互斥，避免调用方同时传两个语义
        conds.append(ContentComment.deleted_at.isnot(None))
    elif not include_deleted:
        conds.append(ContentComment.deleted_at.is_(None))
    if target_type:
        conds.append(ContentComment.target_type == target_type)
    if target_id:
        conds.append(ContentComment.target_id == target_id)
    if start_at is not None:
        conds.append(ContentComment.created_at >= start_at)
    if end_at is not None:
        conds.append(ContentComment.created_at <= end_at)
    if keyword:
        # 转义 LIKE 通配符：不转义时用户搜 "%" 会匹配全部评论（性能与语义双输）
        conds.append(
            ContentComment.content.contains(escape_like(keyword), escape="\\")
        )

    total = (
        await db.execute(select(func.count(ContentComment.id)).where(*conds))
    ).scalar() or 0
    rows = (
        await db.execute(
            select(ContentComment)
            .where(*conds)
            # 回收站按删除时间倒序（「最近删的」先恢复）；普通列表仍按发布时间。
            # 秒级时间必须配 id.desc() 才能跨页稳定（否则误删漏删）
            .order_by(
                *(
                    (ContentComment.deleted_at.desc(), ContentComment.id.desc())
                    if deleted_only
                    else (ContentComment.created_at.desc(), ContentComment.id.desc())
                )
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    author_ids = {c.user_id for c in rows}
    authors: dict[int, User] = {}
    if author_ids:
        authors = {
            u.id: u
            for u in (await db.execute(select(User).where(User.id.in_(author_ids)))).scalars().all()
        }

    # 标题按目标类型分组回查：一次一种类型一条查询，不随评论条数增长。
    # 未知类型（历史脏数据 / 已下线的类型）跳过，不因一条脏数据让整个列表 500。
    ids_by_type: dict[str, set[int]] = {}
    for c in rows:
        ids_by_type.setdefault(c.target_type, set()).add(c.target_id)
    titles: dict[tuple[str, int], str] = {}
    for ttype, ids in ids_by_type.items():
        try:
            spec = _target_spec(ttype)
        except AppException:
            logger.warning(f"评论列表遇到未知 target_type={ttype!r}，跳过标题回查")
            continue
        # 只选 id + 标题两列：内容表带 MEDIUMTEXT 正文与多个 JSON 字段，
        # 整行回查会为了一个标题把大字段全拉回来
        found = (
            await db.execute(
                select(spec.model.id, spec.title_column).where(spec.model.id.in_(ids))
            )
        ).all()
        for tid, title in found:
            titles[(ttype, tid)] = title

    items = [_serialize(c, authors.get(c.user_id), can_manage=True) for c in rows]
    for item in items:
        title = titles.get((item["target_type"], item["target_id"]))
        item["target_title"] = title
        # 过渡期响应别名：旧管理端 bundle 的「所属内容」列读 tutorial_title / tutorial_id，
        # 键消失会静默渲染成空列。仅在 tutorial 行、仅管理端下发（前台不读，公开契约不变宽）；
        # 摘除判据与 tutorial_id 请求参数相同（见 api/admin_comments.py）
        if item["target_type"] == COMMENT_TARGET_TUTORIAL:
            item["tutorial_id"] = item["target_id"]
            item["tutorial_title"] = title
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def restore_comment(db: AsyncSession, comment_id: int) -> dict:
    """恢复被软删除的评论（误删兜底）"""
    comment = await _get_comment(db, comment_id)
    if comment.deleted_at is not None:
        comment.deleted_at = None
        await db.commit()
    return {"id": comment.id, "deleted": False}


async def purge_comments_for_targets(
    db: AsyncSession, target_type: str, target_ids: list[int]
) -> int:
    """内容删除时清理其评论（同一事务内调用，不要单独 commit）。内容都是硬删除、
    评论表无外键级联，不同事务清理时进程崩溃会留孤儿评论。这里用硬删除：内容不存在了，
    保留评论既无展示入口也无追溯价值（单条违规评论的追溯由 deleted_at 软删除覆盖）。
    ⚠️ 条件必须同时含 target_type 与 target_id，否则删 tutorial#1 会连带删掉 node#1 的评论；
    ⚠️ target_type 必须先过注册表校验——裸字符串拼进 WHERE 时手滑写错不报错、只留孤儿数据。"""
    spec = _target_spec(target_type)
    if not target_ids:
        return 0
    result = await db.execute(
        sa_delete(ContentComment)
        .where(
            ContentComment.target_type == target_type,
            ContentComment.target_id.in_(target_ids),
        )
        .execution_options(synchronize_session=False)
    )
    removed = result.rowcount or 0
    # 删 0 行是正常情况（该内容确实没评论），但它是「类型写错/忘登记」的唯一可观测信号，故始终留日志
    logger.bind(
        event="comment_purge",
        target_type=target_type,
        model=spec.model.__name__,
        requested=len(target_ids),
        removed=removed,
    ).info(f"内容删除连带清理评论：type={target_type} 内容数={len(target_ids)} 清理评论数={removed}")
    return removed
