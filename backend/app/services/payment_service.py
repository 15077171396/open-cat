"""支付订单：下单、查单、回调结算、发积分/VIP；以及后台套餐/订单查询。"""

from __future__ import annotations

import base64
import io
import secrets
from datetime import datetime, timedelta, timezone

import qrcode
from loguru import logger
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.payment import PaymentOrder, PaymentProduct
from app.models.user import User
from app.services.admin_audit_service import record_admin_action
from app.services.integration_settings_service import get_payment_config
from app.services.payments import (
    PaymentError,
    PaymentNotification,
    available_channels,
    get_payment_provider,
)
from app.services.point_service import (
    MAX_POINTS_BALANCE,
    TYPE_RECHARGE,
    record_transaction,
)
from app.redis import redis_client
from app.utils.sanitize import escape_like

ORDER_STATUSES = ("pending", "paid", "closed", "refunded", "failed")
PAYMENT_KINDS = ("points", "vip")
_QUERY_THROTTLE_SECONDS = 5


def _utcnow() -> datetime:
    """DB DateTime 存 naive UTC；统一口径，避免 aware/naive 比较抛 TypeError。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _order_no() -> str:
    return f"AC{_utcnow().strftime('%Y%m%d%H%M%S')}{secrets.randbelow(10 ** 6):06d}"


def _qr_data_url(content: str) -> str:
    image = qrcode.make(content)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _product_to_dict(product: PaymentProduct) -> dict:
    return {
        "id": product.id,
        "code": product.code,
        "name": product.name,
        "description": product.description or "",
        "kind": product.kind,
        "amount_cents": int(product.amount_cents or 0),
        "amount": f"{(product.amount_cents or 0) / 100:.2f}",
        "points": int(product.points or 0),
        "vip_days": int(product.vip_days or 0),
        "badge": product.badge or "",
        "is_enabled": bool(product.is_enabled),
        "sort_order": int(product.sort_order or 0),
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
    }


def _order_to_dict(order: PaymentOrder, *, include_pay_info: bool = False) -> dict:
    payload = order.provider_payload or {}
    data = {
        "order_no": order.order_no,
        "product_code": order.product_code,
        "product_name": order.product_name,
        "product_kind": order.product_kind,
        "amount_cents": int(order.amount_cents or 0),
        "amount": f"{(order.amount_cents or 0) / 100:.2f}",
        "points": int(order.points or 0),
        "vip_days": int(order.vip_days or 0),
        "channel": order.channel,
        "status": order.status,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "expires_at": order.expires_at.isoformat() if order.expires_at else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }
    if include_pay_info:
        data["pay_url"] = payload.get("pay_url")
        qr_content = payload.get("qr_content")
        data["qr_data_url"] = _qr_data_url(qr_content) if qr_content else None
        data["qr_content"] = qr_content
    return data


async def list_products(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(PaymentProduct)
            .where(PaymentProduct.is_enabled.is_(True))
            .order_by(PaymentProduct.sort_order, PaymentProduct.id)
        )
    ).scalars().all()
    return [_product_to_dict(row) for row in rows]


async def list_user_orders(db: AsyncSession, user: User, limit: int = 20) -> list[dict]:
    rows = (
        await db.execute(
            select(PaymentOrder)
            .where(PaymentOrder.user_id == user.id)
            .order_by(PaymentOrder.created_at.desc())
            .limit(max(1, min(limit, 50)))
        )
    ).scalars().all()
    return [_order_to_dict(row) for row in rows]


async def get_user_order(db: AsyncSession, user: User, order_no: str) -> PaymentOrder:
    order = (
        await db.execute(
            select(PaymentOrder).where(
                PaymentOrder.order_no == order_no, PaymentOrder.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if order is None:
        raise AppException(code=404, message="订单不存在")
    return order


async def create_order(
    db: AsyncSession,
    user: User,
    *,
    product_code: str,
    channel: str,
    client_ip: str | None = None,
) -> dict:
    config = await get_payment_config()
    if not config.bool("payment_enabled"):
        raise AppException(code=403, message="在线支付未开启")

    product = (
        await db.execute(
            select(PaymentProduct).where(
                PaymentProduct.code == product_code,
                PaymentProduct.is_enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if product is None:
        raise AppException(code=404, message="套餐不存在或已下架")
    if product.amount_cents <= 0:
        raise AppException(code=400, message="套餐金额配置错误")
    if product.kind == "points" and product.points <= 0:
        raise AppException(code=400, message="积分套餐配置错误")
    if product.kind == "vip" and product.vip_days <= 0:
        raise AppException(code=400, message="VIP 套餐配置错误")
    if product.kind not in PAYMENT_KINDS:
        raise AppException(code=400, message="套餐类型配置错误")

    if channel not in {item["value"] for item in available_channels(config)}:
        raise AppException(code=400, message="该支付渠道暂不可用")

    try:
        provider = await get_payment_provider(channel, config)
    except PaymentError as exc:
        raise AppException(code=502, message=str(exc))

    expire_minutes = max(5, min(config.int("payment_order_expire_minutes", default=30), 1440))
    order = PaymentOrder(
        order_no=_order_no(),
        user_id=user.id,
        product_code=product.code,
        product_name=product.name,
        product_kind=product.kind,
        amount_cents=product.amount_cents,
        points=product.points,
        vip_days=product.vip_days,
        channel=channel,
        status="pending",
        expires_at=_utcnow() + timedelta(minutes=expire_minutes),
        client_ip=(client_ip or "")[:45] or None,
    )
    db.add(order)
    await db.flush()

    try:
        intent = await provider.create_payment(order)
    except PaymentError as exc:
        order.status = "failed"
        order.provider_payload = {"error": str(exc)[:200]}
        await db.commit()
        raise AppException(code=502, message=str(exc))

    order.provider_payload = {
        **(intent.provider_payload or {}),
        "pay_url": intent.pay_url,
        "qr_content": intent.qr_content,
    }
    await db.commit()
    # server_default 的 created_at 在 flush 后仍是未加载状态；commit 后序列化会触发
    # 惰性 IO（MissingGreenlet），先刷新再返回
    await db.refresh(order)
    logger.info(f"支付下单: {order.order_no} user={user.id} channel={channel} amount={order.amount_cents}")
    return _order_to_dict(order, include_pay_info=True)


async def refresh_order(db: AsyncSession, order: PaymentOrder) -> None:
    """用户轮询时主动查单：回调丢失也能靠主动查询补发权益。"""
    if order.status != "pending":
        return
    if order.expires_at and order.expires_at < _utcnow():
        order.status = "closed"
        await db.commit()
        return

    throttle_key = f"pay_query:{order.order_no}"
    try:
        if not await redis_client.set(throttle_key, "1", nx=True, ex=_QUERY_THROTTLE_SECONDS):
            return
    except RedisError:
        # Redis 不可用时退化为每次查单；支付回调仍是主路径
        pass

    config = await get_payment_config()
    try:
        provider = await get_payment_provider(order.channel, config)
        notification = await provider.query_order(order)
    except PaymentError as exc:
        logger.warning(f"支付查单失败 {order.order_no}: {exc}")
        return
    if notification is not None and notification.success:
        await settle_order(db, notification)


async def settle_order(db: AsyncSession, notification: PaymentNotification) -> bool:
    """幂等结算：锁订单行 → 校验金额 → 条件迁移到 paid → 同事务发权益。"""
    if not notification.order_no:
        logger.warning("支付回调缺少订单号，已忽略")
        return False

    order = (
        await db.execute(
            select(PaymentOrder)
            .where(PaymentOrder.order_no == notification.order_no)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        logger.warning(f"支付回调订单不存在: {notification.order_no}")
        return False
    if order.status == "paid":
        return True
    if order.status != "pending":
        logger.warning(f"支付回调订单状态不可结算: {order.order_no} status={order.status}")
        return False
    if not notification.success:
        logger.warning(f"支付回调非成功状态: {order.order_no} raw={notification.raw}")
        return False
    if notification.amount_cents is None or int(notification.amount_cents) != order.amount_cents:
        logger.error(
            f"支付金额不一致，拒绝发放: {order.order_no} "
            f"expected={order.amount_cents} got={notification.amount_cents}"
        )
        return False

    order.status = "paid"
    order.provider_trade_no = notification.trade_no
    order.paid_at = _utcnow()
    order.provider_payload = {
        **(order.provider_payload or {}),
        "notify": notification.raw,
        "trade_no": notification.trade_no,
    }
    try:
        await _apply_entitlement(db, order)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.exception(f"支付结算唯一约束冲突: {order.order_no}")
        return False
    except AppException:
        await db.rollback()
        logger.exception(f"支付结算发权益失败: {order.order_no}")
        return False
    logger.info(f"支付成功: {order.order_no} user={order.user_id} amount={order.amount_cents}")
    return True


async def _apply_entitlement(db: AsyncSession, order: PaymentOrder) -> None:
    user = (
        await db.execute(
            select(User).where(User.id == order.user_id, User.deleted_at.is_(None)).with_for_update()
        )
    ).scalar_one_or_none()
    if user is None:
        raise AppException(code=404, message="订单用户不存在")

    if order.product_kind == "points":
        old_balance = int(user.points or 0)
        new_balance = min(old_balance + int(order.points or 0), MAX_POINTS_BALANCE)
        granted = new_balance - old_balance
        user.points = new_balance
        if granted > 0:
            record_transaction(
                db,
                user_id=user.id,
                points=granted,
                balance_after=new_balance,
                type=TYPE_RECHARGE,
                description=f"在线充值 {order.order_no}",
            )
        if granted < int(order.points or 0):
            logger.warning(
                f"积分到账被余额上限截断: user={user.id} order={order.order_no} "
                f"granted={granted} expected={order.points}"
            )
        return

    if order.product_kind == "vip":
        now = _utcnow()
        base = now
        if user.vip_expire_time:
            current = user.vip_expire_time
            if current.tzinfo is not None:
                current = current.replace(tzinfo=None)
            if current > now:
                base = current
        user.vip_expire_time = base + timedelta(days=int(order.vip_days or 0))
        if user.role != "admin":
            user.role = "vip"
        return

    logger.error(f"未知商品类型，未发放权益: {order.order_no} kind={order.product_kind}")


async def mock_pay(db: AsyncSession, user: User, order_no: str) -> dict:
    config = await get_payment_config()
    if not config.bool("payment_enabled") or not config.bool("payment_mock_enabled"):
        raise AppException(code=403, message="Mock 支付未开启")
    from app.config import settings

    if settings.APP_ENV.lower() in ("production", "prod"):
        raise AppException(code=403, message="生产环境禁止 Mock 支付")

    order = await get_user_order(db, user, order_no)
    if order.channel != "mock":
        raise AppException(code=400, message="该订单不是 Mock 支付订单")
    if order.status == "paid":
        return _order_to_dict(order)
    if order.status != "pending":
        raise AppException(code=400, message="订单已关闭或不可支付")

    ok = await settle_order(
        db,
        PaymentNotification(
            order_no=order.order_no,
            trade_no=f"MOCK-{order.order_no}",
            amount_cents=order.amount_cents,
            success=True,
            raw={"mock": True},
        ),
    )
    if not ok:
        raise AppException(code=500, message="模拟支付结算失败")
    await db.refresh(order)
    return _order_to_dict(order)


# ---------------- 管理端：套餐 ----------------


def _validate_product_payload(data: dict, *, partial: bool = False) -> dict:
    out: dict = {}
    if not partial or "name" in data:
        name = str(data.get("name") or "").strip()
        if not name or len(name) > 100:
            raise AppException(code=400, message="套餐名称不能为空且不超过 100 字")
        out["name"] = name
    if not partial or "code" in data:
        code = str(data.get("code") or "").strip().lower()
        import re

        if not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", code):
            raise AppException(code=400, message="套餐标识只能包含小写字母、数字、点、下划线和短横线")
        out["code"] = code
    if not partial or "description" in data:
        out["description"] = str(data.get("description") or "").strip()[:300] or None
    if not partial or "kind" in data:
        kind = str(data.get("kind") or "").strip()
        if kind not in PAYMENT_KINDS:
            raise AppException(code=400, message="套餐类型只能是 points / vip")
        out["kind"] = kind
    if not partial or "amount_cents" in data:
        try:
            amount = int(data.get("amount_cents"))
        except (TypeError, ValueError):
            raise AppException(code=400, message="套餐金额必须是整数（分）")
        if amount <= 0 or amount > 100_000_000:
            raise AppException(code=400, message="套餐金额必须在 1 分到 100 万元之间")
        out["amount_cents"] = amount
    if not partial or "points" in data:
        try:
            points = int(data.get("points") or 0)
        except (TypeError, ValueError):
            raise AppException(code=400, message="积分必须是整数")
        if points < 0 or points > MAX_POINTS_BALANCE:
            raise AppException(code=400, message="积分数超出范围")
        out["points"] = points
    if not partial or "vip_days" in data:
        try:
            vip_days = int(data.get("vip_days") or 0)
        except (TypeError, ValueError):
            raise AppException(code=400, message="VIP 天数必须是整数")
        if vip_days < 0 or vip_days > 3650:
            raise AppException(code=400, message="VIP 天数超出范围")
        out["vip_days"] = vip_days
    if not partial or "badge" in data:
        out["badge"] = str(data.get("badge") or "").strip()[:30] or None
    if not partial or "is_enabled" in data:
        out["is_enabled"] = bool(data.get("is_enabled", True))
    if not partial or "sort_order" in data:
        try:
            out["sort_order"] = int(data.get("sort_order") or 0)
        except (TypeError, ValueError):
            raise AppException(code=400, message="排序必须是整数")

    kind = out.get("kind")
    points = out.get("points")
    vip_days = out.get("vip_days")
    if kind == "points" and (not points or points <= 0):
        raise AppException(code=400, message="积分套餐必须配置大于 0 的积分")
    if kind == "vip" and (not vip_days or vip_days <= 0):
        raise AppException(code=400, message="VIP 套餐必须配置大于 0 的天数")
    return out


async def list_admin_products(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(PaymentProduct).order_by(PaymentProduct.sort_order, PaymentProduct.id)
        )
    ).scalars().all()
    return [_product_to_dict(row) for row in rows]


async def create_product(db: AsyncSession, data: dict, admin: User) -> dict:
    payload = _validate_product_payload(data)
    product = PaymentProduct(**payload)
    db.add(product)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppException(code=409, message="套餐标识已存在")
    record_admin_action(
        db,
        admin,
        "payment_product_create",
        target_type="payment_product",
        target_id=product.id,
        summary=f"新建充值套餐「{product.name}」",
        detail={"code": product.code, "amount_cents": product.amount_cents},
    )
    await db.commit()
    await db.refresh(product)
    return _product_to_dict(product)


async def update_product(db: AsyncSession, product_id: int, data: dict, admin: User) -> dict:
    product = (
        await db.execute(select(PaymentProduct).where(PaymentProduct.id == product_id))
    ).scalar_one_or_none()
    if product is None:
        raise AppException(code=404, message="套餐不存在")
    if data.get("code") and str(data["code"]).strip().lower() != product.code:
        raise AppException(code=400, message="套餐标识创建后不可修改（订单已引用）")
    payload = _validate_product_payload(data, partial=True)
    effective_kind = payload.get("kind", product.kind)
    effective_points = payload.get("points", product.points)
    effective_vip_days = payload.get("vip_days", product.vip_days)
    if effective_kind == "points" and (not effective_points or effective_points <= 0):
        raise AppException(code=400, message="积分套餐必须配置大于 0 的积分")
    if effective_kind == "vip" and (not effective_vip_days or effective_vip_days <= 0):
        raise AppException(code=400, message="VIP 套餐必须配置大于 0 的天数")
    for key, value in payload.items():
        setattr(product, key, value)
    record_admin_action(
        db,
        admin,
        "payment_product_update",
        target_type="payment_product",
        target_id=product.id,
        summary=f"编辑充值套餐「{product.name}」",
        detail={"fields": sorted(payload.keys())},
    )
    await db.commit()
    await db.refresh(product)
    return _product_to_dict(product)


async def delete_product(db: AsyncSession, product_id: int, admin: User) -> None:
    product = (
        await db.execute(select(PaymentProduct).where(PaymentProduct.id == product_id))
    ).scalar_one_or_none()
    if product is None:
        raise AppException(code=404, message="套餐不存在")
    pending = int(
        (
            await db.execute(
                select(func.count()).select_from(PaymentOrder).where(
                    PaymentOrder.product_code == product.code,
                    PaymentOrder.status == "pending",
                )
            )
        ).scalar()
        or 0
    )
    if pending:
        raise AppException(code=409, message="该套餐仍有待支付订单，请先下架或等订单结束")
    name = product.name
    await db.delete(product)
    record_admin_action(
        db,
        admin,
        "payment_product_delete",
        target_type="payment_product",
        target_id=product_id,
        summary=f"删除充值套餐「{name}」",
    )
    await db.commit()


# ---------------- 管理端：订单 ----------------


async def list_admin_orders(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    channel: str | None = None,
    keyword: str | None = None,
) -> dict:
    conditions = []
    if status:
        if status not in ORDER_STATUSES:
            raise AppException(code=400, message="订单状态无效")
        conditions.append(PaymentOrder.status == status)
    if channel:
        conditions.append(PaymentOrder.channel == channel)
    if keyword:
        pattern = f"%{escape_like(keyword)}%"
        conditions.append(
            (PaymentOrder.order_no.like(pattern, escape="\\"))
            | (User.account.like(pattern, escape="\\"))
        )

    count_stmt = select(func.count()).select_from(PaymentOrder).where(*conditions)
    total = int((await db.execute(count_stmt)).scalar() or 0)
    rows = (
        await db.execute(
            select(PaymentOrder, User.account)
            .outerjoin(User, User.id == PaymentOrder.user_id)
            .where(*conditions)
            .order_by(PaymentOrder.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for order, account in rows:
        item = _order_to_dict(order)
        item["user_id"] = order.user_id
        item["user_account"] = account
        item["provider_trade_no"] = order.provider_trade_no
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_admin_order(db: AsyncSession, order_no: str) -> dict:
    row = (
        await db.execute(
            select(PaymentOrder, User.account)
            .outerjoin(User, User.id == PaymentOrder.user_id)
            .where(PaymentOrder.order_no == order_no)
        )
    ).first()
    if row is None:
        raise AppException(code=404, message="订单不存在")
    order, account = row
    item = _order_to_dict(order, include_pay_info=True)
    item["user_id"] = order.user_id
    item["user_account"] = account
    item["provider_trade_no"] = order.provider_trade_no
    item["provider_payload"] = order.provider_payload
    return item
