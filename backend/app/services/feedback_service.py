"""用户问题反馈服务

前台提交（登录用户）与管理后台审阅共用状态/序列化定义。
后台审阅操作见 admin_feedback_service.py。
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.feedback import FEEDBACK_MAX_IMAGES, FEEDBACK_STATUS_LABELS, Feedback
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
