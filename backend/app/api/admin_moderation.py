"""管理后台 - 内容审核中心（机器判定留痕 + 人工举报 + 敏感词库）"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Literal, Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import success_response
from app.models.moderation import (
    CONTENT_TYPE_LABELS,
    MODERATION_STATUSES,
    MODERATION_STATUS_LABELS,
    REPORT_STATUSES,
    REPORT_STATUS_LABELS,
)
from app.schemas.common import ApiResponse
from app.api._admin_shared import router
from app.services.admin_audit_service import record_admin_action

from app.services import moderation_service, moderation_words_service, report_service
from app.schemas.content import PageResult
from app.schemas.admin import ActionAck, ModerationLogItem, ReportItem, StatsResult


# 敏感词库（2026-11-18 起提供管理界面）：读写与热重载逻辑在
# moderation_words_service（路由层禁 ORM，见 test_rbac 架构守卫）。

@router.get("/moderation/logs", response_model=ApiResponse[PageResult[ModerationLogItem]])
async def admin_list_moderation_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """敏感词命中留痕列表（状态/类型筛选）

    响应带 content_type_options / status_options：筛选下拉与中文名都取自**写入端同一份事实来源**，
    否则新增内容类型后管理端下拉静默漏项、列表里显示成裸代码（`comment` 就出过这事）。
    """
    data = await moderation_service.list_logs(db, page, page_size, status, content_type)
    data["content_type_options"] = [{"value": k, "label": v} for k, v in CONTENT_TYPE_LABELS.items()]
    data["status_options"] = [
        {"value": k, "label": MODERATION_STATUS_LABELS[k]} for k in MODERATION_STATUSES
    ]
    return success_response(data)


class ModerationReviewRequest(BaseModel):
    status: Literal["pending", "approved", "rejected"]
    note: Optional[str] = Field(None, max_length=500)


@router.put("/moderation/logs/{log_id}", response_model=ApiResponse[ActionAck])
async def admin_review_moderation_log(
    log_id: int,
    req: ModerationReviewRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """人工复核机器判定（误报放行 / 维持拦截）"""
    result = success_response(
        await moderation_service.review_log(db, log_id, admin.id, req.status, req.note),
        "已提交复核结果",
    )
    # 审计：此步决定机审判违规的内容是否放回公开区，必须记下操作人与判定结果
    record_admin_action(
        db, admin, "moderation_review",
        target_type="moderation_log", target_id=log_id,
        summary=f"复核敏感词留痕 {log_id} → {req.status}",
        detail={"status": req.status},
    )
    return result


@router.get("/reports", response_model=ApiResponse[PageResult[ReportItem]])
async def admin_list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用户举报列表（待处理优先）；筛选下拉与中文名同 logs 接口取自后端"""
    data = await report_service.list_reports(db, page, page_size, status, content_type)
    data["content_type_options"] = [{"value": k, "label": v} for k, v in CONTENT_TYPE_LABELS.items()]
    data["status_options"] = [
        {"value": k, "label": REPORT_STATUS_LABELS[k]} for k in REPORT_STATUSES
    ]
    return success_response(data)


class ReportHandleRequest(BaseModel):
    status: Literal["resolved", "dismissed"]
    note: Optional[str] = Field(None, max_length=500)


@router.put("/reports/{report_id}", response_model=ApiResponse[ActionAck])
async def admin_handle_report(
    report_id: int,
    req: ReportHandleRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """处理举报：resolved=已处理 / dismissed=判定为误报并关闭"""
    result = success_response(
        await report_service.handle_report(db, report_id, admin.id, req.status, req.note),
        "举报已处理",
    )
    # 审计：把举报判成「误报关闭」是让违规内容继续留在线上的动作，必须能回答是谁按什么理由关掉的
    record_admin_action(
        db, admin, "report_handle",
        target_type="report", target_id=report_id,
        summary=f"处理举报 {report_id} → {req.status}",
        detail={"status": req.status},
    )
    return result


@router.get("/moderation/summary", response_model=ApiResponse[StatsResult])
async def admin_moderation_summary(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """审核中心角标：待复核 / 待处理举报数量"""
    return success_response({
        "pending_logs": await moderation_service.count_pending(db),
        "pending_reports": await report_service.count_pending(db),
    })


# ---------------------------------------------------------------------------
# 敏感词库（2026-11-18）：词库 = 内置兜底词 + 后台可维护的自定义词
# ---------------------------------------------------------------------------


class ModerationWordsSaveRequest(BaseModel):
    """保存自定义词（**全量覆盖**：界面把当前词表整体提交）"""

    extra_words: list[str] = Field(default_factory=list, max_length=600)


class ModerationWordsTestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="待检测文本")


@router.get("/moderation/words", response_model=ApiResponse[ActionAck])
async def admin_list_moderation_words(
    admin: User = Depends(require_admin),
):
    """词库清单（内置兜底词 + 自定义词，界面分组展示）"""
    return success_response(moderation_words_service.list_words())


@router.put("/moderation/words", response_model=ApiResponse[ActionAck])
async def admin_save_moderation_words(
    req: ModerationWordsSaveRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """保存自定义敏感词（全量覆盖）。上限 500 个，保存后立即生效并写审计。

    事务边界在 service 层（保存 + 审计 + 提交 + 热生效），API 层不手工 commit。
    """
    result = await moderation_words_service.save_words_with_audit(db, admin, req.extra_words)
    return success_response(
        {"ok": True, "changed": result["changed"], "words": result["words"]}
    )


@router.post("/moderation/words/test", response_model=ApiResponse[ActionAck])
async def admin_test_moderation_words(
    req: ModerationWordsTestRequest,
    admin: User = Depends(require_admin),
):
    """命中测试：对任意文本跑一次当前生效词库（内置 + 自定义），返回命中词。"""
    return success_response({"hits": moderation_words_service.test_text(req.text)})
