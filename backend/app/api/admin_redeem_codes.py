"""管理后台 - 积分兑换码管理"""

from datetime import datetime

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.core.rbac import has_any_permission
from app.models.user import User
from app.core.exceptions import AppException, success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.utils.time import parse_iso_to_naive_utc
from app.api._admin_shared import router

from app.services.redemption_service import (
    create_codes as create_redemption_codes,
    list_codes as list_redemption_codes,
    disable_code as disable_redemption_code,
    disable_codes_batch as disable_redemption_codes_batch,
    list_code_records as list_redemption_code_records,
)
from app.schemas.content import PageResult
from app.schemas.admin import ActionAck, AffectedResult, RedemptionCodeItem, RedemptionRecordItem


# 积分兑换码管理

class RedemptionCodeCreateRequest(BaseModel):
    points: int = Field(..., ge=1, le=1_000_000)
    # 最大兑换人数：=1 单人码（仅1人可兑换） / >1 多人限量码（先到先得）
    max_uses: int = Field(..., ge=1, le=100_000)
    # 批量生成数量
    count: int = Field(1, ge=1, le=100)
    # ISO 字符串；空/缺省表示永不过期
    expires_at: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=200)


def _parse_expires_at(value: Optional[str]) -> Optional[datetime]:
    """解析过期时间 ISO 字符串；空串/None 视为永不过期。

    时区换算统一走 `utils.time.parse_iso_to_naive_utc`：前端 `toISOString()` 带偏移而
    DB 列是 naive，必须转 UTC，否则东八区环境会差 8 小时。
    """
    try:
        return parse_iso_to_naive_utc(value)
    except ValueError:
        raise AppException(code=400, message="过期时间格式无效，请使用标准日期时间")


@router.post("/redemption-codes", response_model=ApiResponse[AffectedResult])
async def admin_create_redemption_codes(
    req: RedemptionCodeCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量创建积分兑换码（单人码/多人限量码，可设过期时间）"""
    items = await create_redemption_codes(
        db,
        admin,
        points=req.points,
        max_uses=req.max_uses,
        count=req.count,
        expires_at=_parse_expires_at(req.expires_at),
        note=req.note,
    )
    return success_response(
        {"items": items, "count": len(items)},
        message=f"成功生成 {len(items)} 个兑换码",
    )


@router.get("/redemption-codes", response_model=ApiResponse[PageResult[RedemptionCodeItem]])
async def admin_list_redemption_codes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    status: str = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """兑换码列表（keyword 按兑换码/备注模糊，status 按展示状态筛选）；
    明文只给 redeem_code:create 权限，只读角色拿掩码（防明文被批量冒用）"""
    reveal = await has_any_permission(db, admin, ["redeem_code:create"])
    result = await list_redemption_codes(db, page, page_size, keyword, status, reveal=reveal)
    return success_response(result)


class RedemptionCodeBatchDisableRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.post("/redemption-codes/batch-disable", response_model=ApiResponse[AffectedResult])
async def admin_batch_disable_redemption_codes(
    req: RedemptionCodeBatchDisableRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量作废兑换码（已作废的自动跳过，响应里区分 disabled / skipped）

    ⚠️ 本路由必须定义在 `/redemption-codes/{code_id}/...` 之前：路径段数虽不同，
    但同层字面量路由排在参数路由之后是仓库通用约定（见 admin.py 的 import 顺序说明）。
    """
    result = await disable_redemption_codes_batch(db, req.ids, admin)
    skipped = f"，跳过 {result['skipped']} 个已作废" if result["skipped"] else ""
    return success_response(result, message=f"已作废 {result['disabled']} 个兑换码{skipped}")


@router.get("/redemption-codes/{code_id}/records", response_model=ApiResponse[PageResult[RedemptionRecordItem]])
async def admin_redemption_code_records(
    code_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """单个兑换码的兑换记录（谁兑换的、何时兑换的）"""
    reveal = await has_any_permission(db, admin, ["redeem_code:create"])
    result = await list_redemption_code_records(db, code_id, page, page_size, reveal=reveal)
    return success_response(result)


@router.patch("/redemption-codes/{code_id}/disable", response_model=ApiResponse[ActionAck])
async def admin_disable_redemption_code(
    code_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """作废兑换码（已兑换用户不受影响，仅阻止后续兑换）"""
    result = await disable_redemption_code(db, code_id, admin)
    return success_response(result, message="兑换码已作废")
