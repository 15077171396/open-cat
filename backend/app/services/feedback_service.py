"""用户问题反馈服务

前台提交（登录用户）与管理后台审阅共用状态/序列化定义。
后台审阅操作见 admin_feedback_service.py。
"""

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.feedback import FEEDBACK_MAX_IMAGES, FEEDBACK_STATUS_LABELS, Feedback
from app.models.feedback_reply import REPLY_SENDERS, FeedbackReply
from app.models.message import Message
from app.services import moderation_service as ms
from app.utils.image_urls import validate_uploaded_image_urls
from app.utils.sanitize import sanitize_content, sanitize_plain_text

# 前台单条反馈长度限制（与前台表单 maxLength 对齐）。
# 2010 = 用户正文上限 2000 + 类型前缀「【其他问题】」最多 6 字 + 余量：
# 前缀由前端在提交时拼进 content，若这里只给 2000，用户写满 2000 字会在提交时才被拒。
MAX_CONTENT_LENGTH = 2010
MAX_CONTACT_LENGTH = 100
# 单用户每日反馈上限（第四轮审查 P2-48）：此前这是全站唯一没有配额的 UGC 入口
# —— 评论 30/日（comment_service）、举报 20/日（report_service），而反馈只要过了
# 全局 300 req/min 认证限流就能被单账号灌到每天数十万行：每行 ≤2010 字符，
# 命中敏感词时还各带一条 content_moderation_logs。既埋掉真实举报，又在 2G 主机上无界涨表。
# 取 10：正常用户一天写不出 10 条不同的问题反馈，而运营需要的是「刷不动」而不是「刚好够」。
MAX_FEEDBACKS_PER_USER_PER_DAY = 10


def to_dict(f: Feedback, user_info: dict | None = None) -> dict:
    data = {
        "id": f.id,
        "user_id": f.user_id,
        "content": f.content,
        "contact": f.contact,
        "images": list(f.images or []),
        "status": f.status,
        # 中文名由后端给：管理端另维护一份映射会在新增状态时静默显示成裸代码
        "status_label": FEEDBACK_STATUS_LABELS.get(f.status, f.status),
        "admin_note": f.admin_note,
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    }
    if user_info is not None:
        data["user_account"] = user_info.get("account")
        data["user_nickname"] = user_info.get("nickname")
    return data


async def _check_daily_quota(db: AsyncSession, user_id: int) -> None:
    """单用户每日反馈上限（与 comment_service / report_service 同口径）。

    ⚠️ 按**自然日**、且用 naive UTC 与 `created_at` 比较：全库按 UTC 写入
    （compose 锁 `TZ=UTC`），拿 aware datetime 去比 naive 列会整体错位或直接抛错。
    """
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    count = (
        await db.execute(
            select(func.count(Feedback.id)).where(
                Feedback.user_id == user_id,
                Feedback.created_at >= today_start,
            )
        )
    ).scalar() or 0
    if count >= MAX_FEEDBACKS_PER_USER_PER_DAY:
        raise AppException(
            code=429,
            message=f"今日反馈已达上限（{MAX_FEEDBACKS_PER_USER_PER_DAY} 条），请明天再来",
        )


async def submit_feedback(
    db: AsyncSession,
    user_id: int,
    content: str,
    contact: str | None,
    images: list[str] | None = None,
) -> dict:
    """提交问题反馈（内容过 nh3 清洗，防存储型 XSS；截图只接受本站上传产出的地址）"""
    content = (content or "").strip()
    # 有截图时正文可以为空：截图本身就能说明问题（如纯视觉错位）
    images = validate_uploaded_image_urls(images, max_count=FEEDBACK_MAX_IMAGES)
    if not content and not images:
        raise AppException(code=400, message="反馈内容不能为空")
    if len(content) > MAX_CONTENT_LENGTH:
        raise AppException(code=400, message=f"反馈内容不能超过 {MAX_CONTENT_LENGTH} 字")

    contact = (contact or "").strip() or None
    if contact and len(contact) > MAX_CONTACT_LENGTH:
        raise AppException(code=400, message=f"联系方式不能超过 {MAX_CONTACT_LENGTH} 字")

    # 敏感词检查（台账 P0-5）：正文与联系方式都过一遍——联系方式正是
    # 「加微信/加QQ」这类引流的高发位置，只查正文等于漏掉一半。
    # 命中留痕单独提交：reject 分支下反馈本体不入库，但留痕要留下，
    # 否则管理员看不到有人在提交违规内容。
    for field, value in (("content", content), ("contact", contact)):
        result = ms.screen_text(value)
        if not result.hits:
            continue
        await ms.log_hit(
            db, content_type="feedback", user_id=user_id,
            text=value, hits=result.hits, action=result.action,
        )
        if not result.passed:
            # 本体不入库，但留痕必须留下，单独提交
            await db.commit()
            raise AppException(
                code=400,
                message="内容包含不当信息，请修改后重新提交",
                data={"hit_words_count": len(result.hits)},
            )
        if field == "content":
            content = result.text      # action=mask 时已打码
        else:
            contact = result.text or None

    # 配额放在审核之后、入库之前：与评论/举报同口径（违规留痕不因超限被跳过），
    # 又保证超限时不产生任何写入
    await _check_daily_quota(db, user_id)

    feedback = Feedback(
        user_id=user_id,
        content=sanitize_content(content),
        # 联系方式在管理后台展示，与 content 同口径清洗防存储型 XSS
        # （防御不一致：渲染端一旦以 HTML 插入即成攻击向量）
        contact=sanitize_plain_text(contact),
        # 图片地址已过白名单校验（必须本站 COS/CDN 前缀），原样入库
        images=images or None,
        status="pending",
    )
    db.add(feedback)
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    return {"id": feedback.id}


# ---------------- 反馈回复（工单闭环，2026-11-03） ----------------
#
# 反馈从「状态 + 内部备注」的单向台账升级为可往返的工单：
# 管理员回复 → 用户站内信收到通知 → 用户可补充说明（把已解决的工单重新拉起）。
# 与 admin_note 的职责分离见 models/feedback_reply.py 模块注释。

REPLY_MAX_LENGTH = 1000

# 用户在「已解决」状态下的回复会把工单重新拉起：用户说「还是不行」时，
# 不应该逼他重写一遍问题描述再开新单
_REOPENING_STATUSES = frozenset({"resolved"})

# 已关闭即终态：再往里写回复只会让「关闭」失去含义（问题已了结 / 重复提交合并）
_CLOSED_STATUS = "closed"


def reply_to_dict(r: FeedbackReply) -> dict:
    return {
        "id": r.id,
        "feedback_id": r.feedback_id,
        "sender": r.sender,
        "content": r.content,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def list_replies(db: AsyncSession, feedback_id: int) -> list[dict]:
    """一条反馈的完整回复线程（时间升序）。"""
    rows = (
        await db.scalars(
            select(FeedbackReply)
            .where(FeedbackReply.feedback_id == feedback_id)
            .order_by(FeedbackReply.created_at.asc(), FeedbackReply.id.asc())
        )
    ).all()
    return [reply_to_dict(r) for r in rows]


async def reply_counts_for(db: AsyncSession, feedback_ids: list[int]) -> dict[int, int]:
    """批量统计回复数（列表页一屏一次查询，不做逐行 N+1）。"""
    if not feedback_ids:
        return {}
    ids = (
        await db.scalars(
            select(FeedbackReply.feedback_id).where(FeedbackReply.feedback_id.in_(feedback_ids))
        )
    ).all()
    return dict(Counter(ids))


async def _notify_feedback_reply(
    db: AsyncSession, *, user_id: int, sender_id: int | None, content: str
) -> None:
    """回复的站内通知（同事务写入，业务回滚通知一起消失）。

    照抄 comment_service._notify_comment_reply 的单条 Message 模式：
    type=system 合法值内的复用（message_service.VALID_TYPES 不扩枚举）；
    link 留空 —— 用户前台的路由不在本仓库，填了反而指到不存在的页面。
    """
    db.add(Message(
        user_id=user_id,
        sender_id=sender_id,
        batch_id=None,
        type="system",
        title="你的反馈有新回复",
        content=content[:120],
        link=None,
    ))


async def add_reply(
    db: AsyncSession,
    *,
    feedback_id: int,
    sender: str,
    sender_id: int | None,
    content: str,
    notify_user: bool,
    commit: bool = True,
) -> dict:
    """工单回复的统一入口（管理员与用户共用，差异只在状态流转方向）。

    - 管理员回复：待处理 → 处理中（回复即跟进）；
    - 用户回复：已解决 → 处理中（用户说没解决，工单重开）；已关闭拒收
      —— 「关闭」是终态，否则它失去含义。
    """
    if sender not in REPLY_SENDERS:
        raise AppException(code=400, message="无效的回复方")
    content = (content or "").strip()
    if not content:
        raise AppException(code=400, message="回复内容不能为空")
    if len(content) > REPLY_MAX_LENGTH:
        raise AppException(code=400, message=f"回复内容不能超过 {REPLY_MAX_LENGTH} 字")

    feedback = (
        await db.scalars(select(Feedback).where(Feedback.id == feedback_id))
    ).first()
    if not feedback:
        raise AppException(code=404, message="反馈不存在")
    if feedback.status == _CLOSED_STATUS:
        raise AppException(code=400, message="该反馈已关闭，如仍有问题请重新提交")

    # 敏感词检查只对用户侧生效：管理员回复是站内运营动作，不走 UGC 审核。
    # 留痕与提交同口径（违规留痕不因失败丢失），命中 reject 时先提交留痕再拒绝。
    if sender == "user":
        result = ms.screen_text(content)
        if result.hits:
            await ms.log_hit(
                db, content_type="feedback_reply", user_id=sender_id or 0,
                text=content, hits=result.hits, action=result.action,
            )
            if not result.passed:
                await db.commit()
                raise AppException(code=400, message="内容包含不当信息，请修改后重新提交")
            content = result.text or content

    if sender == "admin" and feedback.status == "pending":
        feedback.status = "processing"
    elif sender == "user" and feedback.status in _REOPENING_STATUSES:
        feedback.status = "processing"

    reply = FeedbackReply(feedback_id=feedback_id, sender=sender, content=content)
    db.add(reply)
    if notify_user:
        await _notify_feedback_reply(
            db, user_id=feedback.user_id, sender_id=sender_id, content=content,
        )
    # created_at 是 server_default 列：flush 落行 + refresh 取回时间戳后再序列化
    # （真实异步会话在 INSERT 后直接读未加载列会抛 MissingGreenlet）
    await db.flush()
    await db.refresh(reply)
    # 显式提交：与 submit_feedback 同口径（get_db teardown 的 commit 晚于响应发送）。
    # commit=False 供管理路径使用 —— 审计必须与回复同一事务，提交权交给调用方。
    if commit:
        await db.commit()
    return reply_to_dict(reply)


async def list_my_feedbacks(
    db: AsyncSession, user_id: int, page: int = 1, page_size: int = 20,
) -> dict:
    """当前用户的反馈列表（含各自回复线程），供内容 API 使用方渲染工单进度。"""
    conditions = [Feedback.user_id == user_id]
    total = await db.scalar(select(func.count(Feedback.id)).where(*conditions))
    rows = (
        await db.scalars(
            select(Feedback)
            .where(*conditions)
            .order_by(Feedback.created_at.desc(), Feedback.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for f in rows:
        data = to_dict(f)
        data["replies"] = await list_replies(db, f.id)
        items.append(data)
    return {"items": items, "total": total or 0, "page": page, "page_size": page_size}
