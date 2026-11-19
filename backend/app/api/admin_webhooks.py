"""Webhook 订阅管理路由（2026-11-20）：列表 / 新增 / 启停 / 删除 / 试投。

独立授权面（webhook:view / webhook:manage）：订阅决定「站内事件推给谁」，
删错即静默断流 —— 与令牌、与配置面都分开授权。
"""

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import require_elevation, router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.open_api import (
    WebhookCreatedData,
    WebhookDeleteData,
    WebhookListData,
    WebhookStateData,
    WebhookTestResultData,
)
from app.services import webhook_service


class WebhookCreateRequest(BaseModel):
    """新增订阅：名称 + https 回调地址 + 订阅事件子集（空 = 全部事件）"""

    name: str = Field(..., min_length=1, max_length=100, description="用途备注")
    url: str = Field(..., min_length=8, max_length=500, description="回调地址（仅 https）")
    events: list[str] | None = Field(default=None, description="事件名子集；空 = 全部")


class WebhookActiveRequest(BaseModel):
    """启停请求：active=True 启用 / False 停用（停用保留配置）"""

    active: bool


@router.get("/webhooks", response_model=ApiResponse[WebhookListData])
async def admin_list_webhooks(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """订阅清单（含最近投递状态；不含签名密钥）。"""
    return success_response({"items": await webhook_service.list_webhooks(db)})


@router.post("/webhooks", response_model=ApiResponse[WebhookCreatedData])
async def admin_create_webhook(
    req: WebhookCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """新增订阅。**响应里的 secret 是明文唯一一次出现**（用于订阅方验签），提示立即保存。"""
    result = await webhook_service.create_webhook(db, admin, req.name, req.url, req.events)
    return success_response(result)


@router.put("/webhooks/{webhook_id}", response_model=ApiResponse[WebhookStateData])
async def admin_set_webhook_active(
    webhook_id: int,
    req: WebhookActiveRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """启用 / 停用订阅。"""
    result = await webhook_service.set_active(db, admin, webhook_id, req.active)
    return success_response(result)


@router.post("/webhooks/{webhook_id}/test", response_model=ApiResponse[WebhookTestResultData])
async def admin_test_webhook(
    webhook_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """试投一次（真实 HTTP 投递，能暴露 URL/签名/超时问题；写审计）。"""
    result = await webhook_service.test_delivery(db, admin, webhook_id)
    return success_response(result)


@router.delete("/webhooks/{webhook_id}", response_model=ApiResponse[WebhookDeleteData])
async def admin_delete_webhook(
    webhook_id: int,
    _: None = Depends(require_elevation),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除订阅（不进回收站 —— 订阅方拿得到事件清单，重建成本低）。"""
    changed = await webhook_service.delete_webhook(db, admin, webhook_id)
    return success_response({"ok": True, "deleted": changed})
