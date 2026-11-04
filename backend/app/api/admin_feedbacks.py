"""管理后台 - 用户反馈管理"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import AppException, success_response
from app.models.feedback import FEEDBACK_STATUSES, FEEDBACK_STATUS_LABELS
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.utils.time import parse_iso_to_naive_utc
from app.api._admin_shared import router

from app.services.admin_feedback_service import (
    admin_add_reply,
    admin_get_feedback,
    admin_list_feedbacks,
    admin_update_feedback,
    admin_delete_feedback,
)
from app.schemas.content import PageResult
from app.schemas.admin import FeedbackDetailItem, FeedbackItem, FeedbackReplyItem


# 用户反馈管理

class FeedbackUpdateRequest(BaseModel):
    status: Optional[str] = Field(None, description="pending/processing/resolved/closed")
    admin_note: Optional[str] = Field(None, max_length=2000)


class FeedbackReplyRequest(BaseModel):
    # 上限与 feedback_service.REPLY_MAX_LENGTH 对齐，超限请求 422 而不是落库时炸
    content: str = Field(..., min_length=1, max_length=1000, description="回复内容（用户可见）")


@router.get("/feedbacks", response_model=ApiResponse[PageResult[FeedbackItem]])
async def admin_list_feedbacks_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None, max_length=KEYWORD_MAX_LENGTH),
    start_at: Optional[str] = Query(None, max_length=40, description="起始时间 ISO 字符串（含时区偏移）"),
    end_at: Optional[str] = Query(None, max_length=40, description="结束时间 ISO 字符串（含时区偏移）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用户反馈列表（按状态/关键字/提交时间区间筛选）

    响应带 status_options：状态中文名与筛选取值都取自**写入端同一份事实来源**，
    否则新增状态后管理端下拉静默漏项、列表里显示成裸代码。
    """
    try:
        start_dt = parse_iso_to_naive_utc(start_at)
        end_dt = parse_iso_to_naive_utc(end_at)
    except ValueError:
        raise AppException(code=400, message="时间范围格式无效，请使用标准日期时间")
    if start_dt and end_dt and start_dt > end_dt:
        raise AppException(code=400, message="开始时间不能晚于结束时间")
    result = await admin_list_feedbacks(
        db, page, page_size, status, keyword, start_dt, end_dt
    )
    result["status_options"] = [
        {"value": k, "label": FEEDBACK_STATUS_LABELS[k]} for k in FEEDBACK_STATUSES
    ]
    return success_response(result)


@router.put("/feedbacks/{feedback_id}", response_model=ApiResponse[None])
async def admin_update_feedback_api(
    feedback_id: int,
    req: FeedbackUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """审阅反馈：更新处理状态 / 管理员备注"""
    await admin_update_feedback(db, admin, feedback_id, req.model_dump(exclude_unset=True))
    return success_response(message="反馈已更新")


@router.get("/feedbacks/{feedback_id}", response_model=ApiResponse[FeedbackDetailItem])
async def admin_get_feedback_api(
    feedback_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """反馈详情：本体 + 完整回复线程（工单化处理视图）"""
    return success_response(await admin_get_feedback(db, feedback_id))


@router.post("/feedbacks/{feedback_id}/replies", response_model=ApiResponse[FeedbackReplyItem])
async def admin_add_feedback_reply_api(
    feedback_id: int,
    req: FeedbackReplyRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """回复反馈：用户站内信收到通知；待处理状态自动流转为处理中。

    回复是**面向用户**的动作（与 admin_note 的内部备注相对），授权单独成码
    `feedback:reply` —— 能看反馈不等于能代表平台向用户发言。
    """
    reply = await admin_add_reply(db, admin, feedback_id, req.content)
    return success_response(reply, "回复已发送")


@router.delete("/feedbacks/{feedback_id}", response_model=ApiResponse[None])
async def admin_delete_feedback_api(
    feedback_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除反馈"""
    await admin_delete_feedback(db, admin, feedback_id)
    return success_response(message="反馈已删除")
