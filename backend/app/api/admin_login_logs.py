"""登录日志管理路由：列表 / 批量删除。

登录日志是**安全遥测**（谁在什么时候从哪来、成没成），与操作审计互补：
审计回答「管理员做了什么」，登录日志回答「谁试过登录」。只读数据挂
login_log:view；删除等于抹掉安全线索，单独成码 login_log:clear 且必须审计。
"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import AppException, success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import PageResult
from app.schemas.login_log import LoginLogDeleteRequest, LoginLogItem
from app.services import login_log_service
from app.utils.time import parse_iso_to_naive_utc


@router.get("/login-logs", response_model=ApiResponse[PageResult[LoginLogItem]])
async def list_login_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    account: str | None = Query(
        None, max_length=KEYWORD_MAX_LENGTH, description="按账号筛选（输入完整账号，脱敏后精确匹配）"
    ),
    status: str | None = Query(None, description="success / failed / logout；all / 不传表示全部"),
    start_at: str | None = Query(None, max_length=40, description="起始时间 ISO 字符串（含时区偏移）"),
    end_at: str | None = Query(None, max_length=40, description="结束时间 ISO 字符串（含时区偏移）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """登录日志列表（时间倒序）。筛选取值与状态枚举都来自写入端同一份事实来源。"""
    try:
        start_dt = parse_iso_to_naive_utc(start_at)
        end_dt = parse_iso_to_naive_utc(end_at)
    except ValueError:
        raise AppException(code=400, message="时间范围格式无效，请使用标准日期时间")
    if start_dt and end_dt and start_dt > end_dt:
        raise AppException(code=400, message="开始时间不能晚于结束时间")
    return success_response(
        await login_log_service.list_logs(
            db,
            page=page,
            page_size=page_size,
            account=account,
            status=status,
            created_after=start_dt,
            created_before=end_dt,
        )
    )


@router.delete("/login-logs", response_model=ApiResponse[ActionAck])
async def delete_login_logs(
    req: LoginLogDeleteRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量删除登录日志（抹掉安全线索的动作，必须能回答「谁删的」）。

    事务边界在 service 层（删除 + 审计 + 提交），API 层不手工 commit。
    """
    deleted = await login_log_service.delete_logs_with_audit(db, admin, req.ids)
    return success_response({"ok": True, "deleted": deleted})
