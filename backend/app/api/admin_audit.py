"""管理操作审计查询路由。只读接口：审计日志不允许修改或删除（能删的审计等于没有审计）。

⚠️ 本模块必须自己挂 check_route_permission：router 级 dependencies 不跨 include_router 继承；
且 audit:view 刻意不授予只读管理员（审计日志会暴露谁正在被调查），漏挂会让该设计失效。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import resolve_data_scope
from app.core.exceptions import AppException, success_response
from app.core.permissions import require_admin
from app.core.rbac import check_route_permission
from app.database import get_db
from app.models.admin_audit_log import (
    AUDIT_ACTIONS,
    AUDIT_RISK_LABELS,
    AUDIT_RISKS,
    AUDIT_TARGET_TYPE_LABELS,
)
from app.models.user import User
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.utils.time import parse_iso_to_naive_utc
from app.services import admin_audit_service
from app.schemas.content import PageResult
from app.schemas.admin import AuditLogItem

router = APIRouter(dependencies=[Depends(check_route_permission)])


@router.get("/audit-logs", response_model=ApiResponse[PageResult[AuditLogItem]])
async def list_admin_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: str | None = Query(None, description="动作码；all / 不传表示全部"),
    admin_id: int | None = Query(None, description="按操作者用户ID筛选"),
    target_type: str | None = Query(None, description="目标类型；all / 不传表示全部"),
    risk: str | None = Query(None, description="风险等级：danger / money / normal；all / 不传表示全部"),
    keyword: str | None = Query(None, max_length=KEYWORD_MAX_LENGTH, description="匹配摘要或操作者账号"),
    start_at: str | None = Query(None, max_length=40, description="起始时间 ISO 字符串（含时区偏移）"),
    end_at: str | None = Query(None, max_length=40, description="结束时间 ISO 字符串（含时区偏移）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """管理操作审计列表（按时间倒序）。

    响应带 action_options / target_type_options / risk_options：筛选取值与展示文案都来自
    **写入端同一份事实来源**，否则新增动作码 / 目标类型后前端下拉静默漏项、中文名显示成裸代码。
    """
    try:
        start_dt = parse_iso_to_naive_utc(start_at)
        end_dt = parse_iso_to_naive_utc(end_at)
    except ValueError:
        raise AppException(code=400, message="时间范围格式无效，请使用标准日期时间")
    if start_dt and end_dt and start_dt > end_dt:
        raise AppException(code=400, message="开始时间不能晚于结束时间")
    data = await admin_audit_service.list_audit_logs(
        db,
        page=page,
        page_size=page_size,
        action=action,
        admin_id=admin_id,
        target_type=target_type,
        keyword=keyword,
        risk=risk,
        start_at=start_dt,
        end_at=end_dt,
        # 数据范围：只给了「审计查看」权限的角色，不该能翻别人的操作记录
        scope=await resolve_data_scope(db, admin),
    )
    data["action_options"] = [{"value": k, "label": v} for k, v in AUDIT_ACTIONS.items()]
    # 带 label：前端不必再维护一份「类型代码 → 中文名」，避免新增类型时显示成裸代码
    data["target_type_options"] = [
        {"value": k, "label": v} for k, v in AUDIT_TARGET_TYPE_LABELS.items()
    ]
    data["risk_options"] = [{"value": k, "label": AUDIT_RISK_LABELS[k]} for k in AUDIT_RISKS]
    return success_response(data)
