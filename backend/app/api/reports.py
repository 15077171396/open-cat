"""用户举报路由：POST /reports 提交举报（需登录）、GET /reports/mine 我举报过的内容；
举报是敏感词过滤漏判时的人工兜底通道"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.core.exceptions import success_response
from app.core.rate_limit import check_user_daily_quota
from app.models.moderation import CONTENT_TYPES, REPORT_REASON_LABELS
from app.models.user import User
from app.schemas.common import ApiResponse
from app.services import report_service
from app.schemas.content import PageResult
from app.schemas.admin import ActionAck, CreatedResult, ReportItem

router = APIRouter()

# 每日举报上限（第二轮复审 S-01）。
# 服务层已有「同一用户对同一内容仅一次」的去重，但去重挡不住「把全站内容挨个举报一遍」；
# 上限按内容量级给足余量，同时保证后台审核队列不会被单个账号刷爆。
MAX_REPORTS_PER_USER_PER_DAY = 50


# 取值由白名单派生：手写的取值列表已经漏掉过 comment，继续手写只会再漏一次
# （同 api/comments.py 的 _TARGET_TYPE_DESC 口径）
_CONTENT_TYPE_DESC = "举报对象类型，取值：" + "/".join(CONTENT_TYPES)


class ReportCreateRequest(BaseModel):
    content_type: str = Field(..., description=_CONTENT_TYPE_DESC)
    target_id: str = Field(..., min_length=1, max_length=64, description="举报对象ID")
    reason: str = Field(..., description="spam/abuse/illegal/infringement/other")
    detail: str | None = Field(None, max_length=500)


@router.post("", response_model=ApiResponse[CreatedResult])
async def create_report(
    req: ReportCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交举报（同一用户对同一内容仅一次）"""
    await check_user_daily_quota(
        current_user.id,
        scope="report",
        limit=MAX_REPORTS_PER_USER_PER_DAY,
        message=f"今日举报次数已达上限（{MAX_REPORTS_PER_USER_PER_DAY} 次），请明天再试",
    )
    result = await report_service.submit_report(
        db,
        current_user.id,
        content_type=req.content_type,
        target_id=req.target_id,
        reason=req.reason,
        detail=req.detail,
    )
    return success_response(result, "举报已提交，我们会尽快核实")


@router.get("/options", response_model=ApiResponse[ActionAck])
async def report_options():
    """举报表单选项：内容类型与理由枚举（前端下拉用，避免硬编码漂移）"""
    return success_response({
        "content_types": list(CONTENT_TYPES),
        "reasons": [
            {"value": value, "label": label}
            for value, label in REPORT_REASON_LABELS.items()
        ],
    })


@router.get("/mine", response_model=ApiResponse[PageResult[ReportItem]])
async def my_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """我提交的举报（含处理状态，让用户看到举报确实被跟进）"""
    return success_response(
        await report_service.list_my_reports(db, current_user.id, page, page_size)
    )
