"""管理后台 - 内容评论管理。目标用 (target_type, target_id) 表达，筛选口径与前台 /comments 一致；
target_type 不做白名单校验（非法值只会筛出 0 条）；保留已废弃的 tutorial_id 参数防旧标签页筛选静默失效"""

from typing import Optional

from fastapi import Depends, Query
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.legacy_metrics import record_legacy_usage
from app.core.permissions import require_admin
from app.models.comment import COMMENT_TARGET_TUTORIAL, COMMENT_TARGET_TYPE_MAX_LENGTH
from app.models.user import User
from app.core.exceptions import AppException, success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.utils.time import parse_iso_to_naive_utc
from app.api._admin_shared import router
from app.services.admin_audit_service import record_admin_action

from app.services import comment_service
from app.schemas.content import PageResult
from app.schemas.admin import AdminCommentItem, DeletedResult


# 内容评论管理


@router.get("/comments", response_model=ApiResponse[PageResult[AdminCommentItem]])
async def admin_list_comments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: Optional[str] = Query(None, max_length=KEYWORD_MAX_LENGTH),
    target_type: Optional[str] = Query(None, max_length=COMMENT_TARGET_TYPE_MAX_LENGTH),
    target_id: Optional[int] = Query(None),
    # 过渡期兼容参数：已打开的旧管理端标签页仍带 tutorial_id 发请求，FastAPI 对未声明参数静默忽略，
    # 不接住它会出现「筛选框有值、列表却是全部评论」，审核者极易误删；摘除判据: admin_legacy_param 计数连续 7 天为 0
    tutorial_id: Optional[int] = Query(None, description="[已废弃] 等价于 target_id + target_type=tutorial"),
    include_deleted: bool = Query(False, description="是否包含已删除评论"),
    start_at: Optional[str] = Query(None, max_length=40, description="起始时间 ISO 字符串（含时区偏移）"),
    end_at: Optional[str] = Query(None, max_length=40, description="结束时间 ISO 字符串（含时区偏移）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """评论列表（可搜索正文、按内容类型/ID 筛选、查看已删除项、按发布时间区间筛选）"""
    try:
        start_dt = parse_iso_to_naive_utc(start_at)
        end_dt = parse_iso_to_naive_utc(end_at)
    except ValueError:
        raise AppException(code=400, message="时间范围格式无效，请使用标准日期时间")
    if start_dt and end_dt and start_dt > end_dt:
        raise AppException(code=400, message="开始时间不能晚于结束时间")
    if tutorial_id is not None:
        logger.bind(event="admin_legacy_param", param="tutorial_id", endpoint="admin_list_comments").info(
            "管理端评论列表仍使用 tutorial_id（旧 bundle 流量）"
        )
        # 计数口径与日志一致：只要还发来 tutorial_id 就算旧流量，等它归零才安全摘除
        await record_legacy_usage("admin_legacy_param")
        # 旧参数只在**没有**新参数时才生效，避免同一条请求里两个口径打架
        if target_id is None:
            target_id = tutorial_id
        if not target_type:
            target_type = COMMENT_TARGET_TUTORIAL
    return success_response(
        await comment_service.admin_list_comments(
            db, page, page_size, keyword, target_type, target_id, include_deleted, start_dt, end_dt
        )
    )


@router.delete("/comments/{comment_id}", response_model=ApiResponse[DeletedResult])
async def admin_delete_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除评论（软删除，可恢复）"""
    result = success_response(
        await comment_service.delete_comment(db, comment_id, admin.id, is_admin=True),
        "评论已删除",
    )
    # 审计：此前删评论零留痕，违规评论被谁处理无法回答；get_db 依赖退出时统一提交，路由层补记
    record_admin_action(
        db, admin, "comment_delete",
        target_type="comment", target_id=comment_id,
        summary=f"删除评论 {comment_id}（连带软删其回复）",
    )
    return result


@router.post("/comments/{comment_id}/restore", response_model=ApiResponse[DeletedResult])
async def admin_restore_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """恢复误删的评论"""
    result = success_response(
        await comment_service.restore_comment(db, comment_id), "评论已恢复"
    )
    # 审计：恢复比删除更敏感（把被判违规的内容重新放回公开区），必须留痕
    record_admin_action(
        db, admin, "comment_restore",
        target_type="comment", target_id=comment_id,
        summary=f"恢复评论 {comment_id}",
    )
    return result
