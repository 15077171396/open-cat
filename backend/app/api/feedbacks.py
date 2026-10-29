"""用户反馈路由

- POST /feedbacks  提交问题反馈（需登录）
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.core.exceptions import success_response
from app.core.rate_limit import check_user_daily_quota
from app.models.user import User
from app.schemas.common import ApiResponse
from app.services.feedback_service import submit_feedback
from app.schemas.admin import CreatedResult

router = APIRouter()

# 每日提交上限（第二轮复审 S-01）。
# 此前只有全局限流（300 次/分钟），一个脚本账号十分钟就能把后台反馈队列灌满，
# 每条还会触发敏感词检查与通知。反馈是低频动作，20 条/天足够覆盖真实用户。
MAX_FEEDBACKS_PER_USER_PER_DAY = 20


class FeedbackCreateRequest(BaseModel):
    # 有截图时正文可以为空（纯视觉问题直接贴图），具体校验在 feedback_service
    # 2010 = 正文 2000 + 类型前缀（前端提交时拼进 content，见 feedback_service.MAX_CONTENT_LENGTH）
    content: str = Field("", max_length=2010)
    contact: str | None = Field(None, max_length=100)
    # 截图 URL 列表：只能填本站上传接口返回的地址，服务端会再校验域名与数量
    images: list[str] | None = Field(None, max_length=3)


@router.post("", response_model=ApiResponse[CreatedResult])
async def create_feedback(
    req: FeedbackCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交问题反馈（右侧工具栏弹窗 / 意见反馈页）"""
    # 配额检查放在写库之前：放在之后等于"每次浪费都免费"
    await check_user_daily_quota(
        current_user.id,
        scope="feedback",
        limit=MAX_FEEDBACKS_PER_USER_PER_DAY,
        message=f"今日反馈提交次数已达上限（{MAX_FEEDBACKS_PER_USER_PER_DAY} 次），请明天再试",
    )
    result = await submit_feedback(db, current_user.id, req.content, req.contact, req.images)
    return success_response(result, "反馈已提交，感谢您的支持")
