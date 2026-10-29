"""用户端支付接口 + 微信/支付宝异步通知（通知端点不套 ApiResponse）。"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, success_response
from app.core.rate_limit import get_client_ip
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.payment import (
    CreatePaymentOrderRequest,
    PaymentCapabilitiesData,
    PaymentOrderData,
    PaymentProductListData,
    PaymentUserOrdersData,
)
from app.services import payment_service
from app.services.integration_settings_service import get_payment_config
from app.services.payments import PaymentError, available_channels, get_payment_provider
from loguru import logger

router = APIRouter()


@router.get("/capabilities", response_model=ApiResponse[PaymentCapabilitiesData])
async def payment_capabilities():
    config = await get_payment_config()
    return success_response(
        {
            "enabled": config.bool("payment_enabled"),
            "channels": available_channels(config),
        }
    )


@router.get("/products", response_model=ApiResponse[PaymentProductListData])
async def list_payment_products(db: AsyncSession = Depends(get_db)):
    return success_response({"items": await payment_service.list_products(db)})


@router.get("/orders", response_model=ApiResponse[PaymentUserOrdersData])
async def list_my_payment_orders(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return success_response({"items": await payment_service.list_user_orders(db, user)})


@router.post("/orders", response_model=ApiResponse[PaymentOrderData])
async def create_payment_order(
    req: CreatePaymentOrderRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await payment_service.create_order(
        db,
        user,
        product_code=req.product_code,
        channel=req.channel,
        client_ip=get_client_ip(request),
    )
    return success_response(result, "订单已创建")


@router.get("/orders/{order_no}", response_model=ApiResponse[PaymentOrderData])
async def get_payment_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = await payment_service.get_user_order(db, user, order_no)
    await payment_service.refresh_order(db, order)
    await db.refresh(order)
    return success_response(payment_service._order_to_dict(order, include_pay_info=True))


@router.post("/orders/{order_no}/mock-pay", response_model=ApiResponse[PaymentOrderData])
async def mock_pay_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return success_response(await payment_service.mock_pay(db, user, order_no), "模拟支付成功")


@router.post("/notify/wechat")
async def wechat_pay_notify(request: Request, db: AsyncSession = Depends(get_db)):
    """微信支付异步通知：验签解密 → 幂等结算 → 原生 ACK（不能套 ApiResponse）。"""
    body = await request.body()
    try:
        provider = await get_payment_provider("wechat", require_enabled=False)
        notification = await provider.parse_notify(body=body, headers=request.headers)
        await payment_service.settle_order(db, notification)
    except (PaymentError, AppException) as exc:
        logger.warning(f"微信支付回调处理失败: {exc}")
        return JSONResponse({"code": "FAIL", "message": "处理失败"}, status_code=400)
    return JSONResponse({"code": "SUCCESS", "message": "成功"})


@router.post("/notify/alipay")
async def alipay_notify(request: Request, db: AsyncSession = Depends(get_db)):
    """支付宝异步通知：form 验签 → 幂等结算 → 纯文本 success/failure。"""
    form = await request.form()
    try:
        provider = await get_payment_provider("alipay", require_enabled=False)
        notification = await provider.parse_notify(form=dict(form))
        await payment_service.settle_order(db, notification)
    except (PaymentError, AppException) as exc:
        logger.warning(f"支付宝回调处理失败: {exc}")
        return PlainTextResponse("failure", status_code=400)
    return PlainTextResponse("success")
