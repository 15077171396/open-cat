"""Stripe Checkout 支付（测试/生产模式）——Checkout Session + Webhook 验签。

设计要点：
- 不引入 Stripe SDK，直接用 httpx 调 REST，API 版本固定，避免额外依赖与自动升级；
- secret key / webhook secret 只在服务端使用，错误信息与日志里绝不回显；
- 本地没有公网 webhook 地址时，前端成功回跳后调用查单接口即可完成开通。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Mapping

import httpx
from loguru import logger

from app.services.integration_settings_service import IntegrationConfig
from app.services.payments.base import (
    PaymentError,
    PaymentIntent,
    PaymentNotification,
    PaymentProvider,
)

#: 固定 Stripe API 版本，避免账号默认版本变化导致字段漂移
_STRIPE_API_VERSION = "2024-06-20"
_STRIPE_API_BASE = "https://api.stripe.com"
#: Webhook 时间戳容差（秒），防止重放旧事件
_WEBHOOK_TOLERANCE = 300


class StripeProvider(PaymentProvider):
    channel = "stripe"

    def __init__(self, config: IntegrationConfig):
        self.config = config
        self.secret_key = config.secret("stripe_secret_key").strip()
        self.publishable_key = config.str("stripe_publishable_key").strip()
        self.webhook_secret = config.secret("stripe_webhook_secret").strip()
        self.currency = (config.str("stripe_currency") or "cny").strip().lower()
        self.mode = (config.str("stripe_mode") or "test").strip().lower()
        missing = [
            name
            for name, value in (
                ("Secret Key", self.secret_key),
                ("Publishable Key", self.publishable_key),
            )
            if not value
        ]
        if missing:
            raise PaymentError(f"Stripe 支付参数不完整：{'、'.join(missing)}")
        if not self.secret_key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
            raise PaymentError("Stripe Secret Key 格式不正确")

        site = config.site_base_url.rstrip("/")
        self.success_url = config.str("stripe_success_url") or (
            f"{site}/recharge/result?provider=stripe"
        )
        self.cancel_url = config.str("stripe_cancel_url") or (
            f"{site}/recharge/result?provider=stripe&status=cancel"
        )

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _with_session_id(url: str) -> str:
        """Stripe 只会替换 success_url 中的 {CHECKOUT_SESSION_ID}。"""
        if "{CHECKOUT_SESSION_ID}" in url:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}session_id={{CHECKOUT_SESSION_ID}}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Stripe-Version": _STRIPE_API_VERSION,
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(
                    method,
                    f"{_STRIPE_API_BASE}{path}",
                    headers=headers,
                    data=dict(data or {}),
                )
        except httpx.HTTPError as exc:
            logger.warning(f"Stripe 请求失败: {type(exc).__name__}")
            raise PaymentError("Stripe 网络请求失败，请稍后重试") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise PaymentError("Stripe 返回内容无法解析") from exc

        if response.status_code >= 400:
            error = payload.get("error") if isinstance(payload, dict) else None
            message = (error or {}).get("message") if isinstance(error, dict) else None
            # 只回传 Stripe 的业务错误文案，绝不带请求头/密钥
            raise PaymentError(f"Stripe 拒绝请求：{message or '未知错误'}")
        if not isinstance(payload, dict):
            raise PaymentError("Stripe 返回内容格式不正确")
        return payload

    # ------------------------------------------------------------------ 下单
    async def create_payment(self, order) -> PaymentIntent:
        payload = await self._request(
            "POST",
            "/v1/checkout/sessions",
            data={
                "mode": "payment",
                "success_url": self._with_session_id(self.success_url),
                "cancel_url": self.cancel_url,
                "client_reference_id": order.order_no,
                "metadata[order_no]": order.order_no,
                "metadata[user_id]": str(order.user_id),
                "payment_intent_data[metadata][order_no]": order.order_no,
                "line_items[0][quantity]": "1",
                "line_items[0][price_data][currency]": self.currency,
                "line_items[0][price_data][unit_amount]": str(order.amount_cents),
                "line_items[0][price_data][product_data][name]": order.product_name[:200],
            },
        )
        session_id = str(payload.get("id") or "")
        checkout_url = str(payload.get("url") or "")
        if not session_id or not checkout_url:
            raise PaymentError("Stripe 未返回 Checkout Session 地址")
        return PaymentIntent(
            pay_url=checkout_url,
            provider_payload={
                "session_id": session_id,
                "mode": self.mode,
                "currency": self.currency,
            },
        )

    # ------------------------------------------------------------------ 查单
    def _session_id(self, order) -> str:
        payload = getattr(order, "provider_payload", None) or {}
        return str(getattr(order, "provider_session_id", "") or payload.get("session_id") or "")

    async def query_order(self, order) -> PaymentNotification | None:
        session_id = self._session_id(order)
        if not session_id:
            return None
        try:
            session = await self._request("GET", f"/v1/checkout/sessions/{session_id}")
        except PaymentError:
            # 查单失败不应把本地订单直接判死，交给超时逻辑处理
            return None

        status = str(session.get("status") or "")
        payment_status = str(session.get("payment_status") or "")
        if status == "complete" and payment_status == "paid":
            return PaymentNotification(
                order_no=order.order_no,
                trade_no=str(session.get("payment_intent") or "") or None,
                amount_cents=session.get("amount_total"),
                success=True,
                raw={
                    "event": "checkout.session.completed",
                    "session_id": session_id,
                    "session_status": status,
                    "payment_status": payment_status,
                },
                currency=str(session.get("currency") or "") or None,
            )
        if status == "expired":
            return PaymentNotification(
                order_no=order.order_no,
                trade_no=None,
                amount_cents=None,
                success=False,
                raw={
                    "event": "checkout.session.expired",
                    "session_id": session_id,
                    "session_status": status,
                    "reason": "expired",
                },
                currency=str(session.get("currency") or "") or None,
            )
        return None

    # ------------------------------------------------------------------ Webhook
    def _verify_signature(self, body: bytes, header: str) -> str:
        """Stripe-Signature: t=...,v1=...；返回事件 JSON 文本。"""
        if not self.webhook_secret:
            raise PaymentError("Stripe Webhook Secret 未配置")
        timestamp = ""
        signatures: list[str] = []
        for part in (header or "").split(","):
            key, _, value = part.strip().partition("=")
            if key == "t":
                timestamp = value
            elif key == "v1":
                signatures.append(value)
        if not timestamp or not signatures:
            raise PaymentError("Stripe 回调签名格式不正确")
        try:
            event_time = int(timestamp)
        except ValueError as exc:
            raise PaymentError("Stripe 回调签名时间戳不正确") from exc
        if abs(time.time() - event_time) > _WEBHOOK_TOLERANCE:
            raise PaymentError("Stripe 回调已过期")

        signed = f"{timestamp}.".encode("utf-8") + body
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"), signed, hashlib.sha256
        ).hexdigest()
        if not any(hmac.compare_digest(expected, item) for item in signatures):
            raise PaymentError("Stripe 回调验签失败")
        return body.decode("utf-8", errors="replace")

    async def parse_notify(
        self,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
    ) -> PaymentNotification:
        header = ""
        if headers:
            # Starlette Headers 大小写不敏感；普通 dict 需要兼容两种写法
            header = headers.get("stripe-signature") or headers.get("Stripe-Signature") or ""
        text = self._verify_signature(body or b"", header)
        try:
            event = json.loads(text)
        except ValueError as exc:
            raise PaymentError("Stripe 回调内容不是合法 JSON") from exc

        event_type = str(event.get("type") or "")
        session = (event.get("data") or {}).get("object") or {}
        if not isinstance(session, dict):
            session = {}
        order_no = str(
            session.get("client_reference_id")
            or (session.get("metadata") or {}).get("order_no")
            or ""
        )
        session_id = str(session.get("id") or "")
        currency = str(session.get("currency") or "") or None
        base_raw = {
            "event": event_type,
            "event_id": event.get("id"),
            "session_id": session_id,
            "session_status": session.get("status"),
            "payment_status": session.get("payment_status"),
        }

        if event_type in (
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        ):
            paid = event_type == "checkout.session.async_payment_succeeded" or str(
                session.get("payment_status") or ""
            ) == "paid"
            if not paid:
                # 异步支付方式（如银行转账）会先 complete 后 paid，此时应保持 pending
                return PaymentNotification(
                    order_no=order_no,
                    trade_no=str(session.get("payment_intent") or "") or None,
                    amount_cents=session.get("amount_total"),
                    success=False,
                    raw={**base_raw, "ignored": True, "reason": "processing"},
                    currency=currency,
                )
            return PaymentNotification(
                order_no=order_no,
                trade_no=str(session.get("payment_intent") or "") or None,
                amount_cents=session.get("amount_total"),
                success=True,
                raw=base_raw,
                currency=currency,
            )
        if event_type == "checkout.session.async_payment_failed":
            return PaymentNotification(
                order_no=order_no,
                trade_no=str(session.get("payment_intent") or "") or None,
                amount_cents=session.get("amount_total"),
                success=False,
                raw={**base_raw, "reason": "failed"},
                currency=currency,
            )
        if event_type == "checkout.session.expired":
            return PaymentNotification(
                order_no=order_no,
                trade_no=None,
                amount_cents=None,
                success=False,
                raw={**base_raw, "reason": "expired"},
                currency=currency,
            )
        # 其它事件（payment_intent.* 等）确认收到但不处理
        return PaymentNotification(
            order_no=order_no,
            trade_no=None,
            amount_cents=None,
            success=False,
            raw={**base_raw, "ignored": True},
        )

