"""管理后台 - 充值套餐 / 支付订单（只读）"""

from typing import Optional

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.payment import (
    PaymentOrderData,
    PaymentOrderListData,
    PaymentProductData,
    PaymentProductListData,
    PaymentProductWriteRequest,
)
from app.services import payment_service


@router.get("/payment-products", response_model=ApiResponse[PaymentProductListData])
async def admin_list_payment_products(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response({"items": await payment_service.list_admin_products(db)})


@router.post("/payment-products", response_model=ApiResponse[PaymentProductData])
async def admin_create_payment_product(
    req: PaymentProductWriteRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await payment_service.create_product(db, req.model_dump(), admin)
    return success_response(result, "套餐已创建")


@router.put("/payment-products/{product_id}", response_model=ApiResponse[PaymentProductData])
async def admin_update_payment_product(
    product_id: int,
    req: PaymentProductWriteRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await payment_service.update_product(
        db, product_id, req.model_dump(exclude_unset=True), admin
    )
    return success_response(result, "套餐已更新")


@router.delete("/payment-products/{product_id}", response_model=ApiResponse[ActionAck])
async def admin_delete_payment_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    await payment_service.delete_product(db, product_id, admin)
    return success_response({"ok": True}, "套餐已删除")


@router.get("/payment-orders", response_model=ApiResponse[PaymentOrderListData])
async def admin_list_payment_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None, max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(
        await payment_service.list_admin_orders(
            db,
            page=page,
            page_size=page_size,
            status=status,
            channel=channel,
            keyword=keyword,
        )
    )


@router.get("/payment-orders/{order_no}", response_model=ApiResponse[PaymentOrderData])
async def admin_get_payment_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return success_response(await payment_service.get_admin_order(db, order_no))
