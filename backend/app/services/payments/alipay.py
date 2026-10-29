"""支付宝电脑网站支付（alipay.trade.page.pay）——RSA2 签名 + 异步通知验签。"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping
from urllib.parse import urlencode

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from loguru import logger

from app.services.integration_settings_service import IntegrationConfig
from app.services.payments.base import (
    PaymentError,
    PaymentIntent,
    PaymentNotification,
    PaymentProvider,
)

_BEIJING = timezone(timedelta(hours=8))


def _normalize_pem(raw: str, kind: str) -> str:
    """把支付宝后台复制的一行 base64 补成 PEM。"""
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("-----BEGIN"):
        return text
    body = "".join(text.split())
    lines = "\n".join(body[index : index + 64] for index in range(0, len(body), 64))
    label = "PRIVATE KEY" if kind == "private" else "PUBLIC KEY"
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----"


def _load_private_key(raw: str):
    pem = _normalize_pem(raw, "private")
    try:
        return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError):
        # 少数工具导出 PKCS#1（BEGIN RSA PRIVATE KEY）
        try:
            pkcs1 = pem.replace("BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY").replace(
                "END PRIVATE KEY", "END RSA PRIVATE KEY"
            )
            return serialization.load_pem_private_key(pkcs1.encode("utf-8"), password=None)
        except (ValueError, TypeError) as exc:
            raise PaymentError("支付宝应用私钥格式不正确") from exc


def _load_public_key(raw: str):
    pem = _normalize_pem(raw, "public")
    try:
        return serialization.load_pem_public_key(pem.encode("utf-8"))
    except ValueError:
        try:
            pkcs1 = pem.replace("BEGIN PUBLIC KEY", "BEGIN RSA PUBLIC KEY").replace(
                "END PUBLIC KEY", "END RSA PUBLIC KEY"
            )
            return serialization.load_pem_public_key(pkcs1.encode("utf-8"))
        except ValueError:
            try:
                return x509.load_pem_x509_certificate(pem.encode("utf-8")).public_key()
            except ValueError as exc:
                raise PaymentError("支付宝公钥格式不正确") from exc


def _amount_to_cents(value: str | int | float | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return None


class AlipayProvider(PaymentProvider):
    channel = "alipay"

    def __init__(self, config: IntegrationConfig):
        self.config = config
        self.app_id = config.str("alipay_app_id")
        self.gateway = config.str("alipay_gateway") or "https://openapi.alipay.com/gateway.do"
        self.notify_url = config.str("alipay_notify_url") or (
            f"{config.site_base_url}/api/v1/payments/notify/alipay"
        )
        self.return_url = config.str("alipay_return_url") or (
            f"{config.site_base_url}/recharge/result"
        )
        private_key = config.secret("alipay_private_key")
        public_key = config.str("alipay_public_key")
        missing = [
            name
            for name, value in (
                ("AppID", self.app_id),
                ("应用私钥", private_key),
                ("支付宝公钥", public_key),
            )
            if not value
        ]
        if missing:
            raise PaymentError(f"支付宝支付参数不完整：{'、'.join(missing)}")
        self._private_key = _load_private_key(private_key)
        self._public_key = _load_public_key(public_key)

    def _sign(self, params: dict[str, str]) -> str:
        content = "&".join(
            f"{key}={params[key]}"
            for key in sorted(params)
            if key not in ("sign", "sign_type") and params[key] not in (None, "")
        )
        signature = self._private_key.sign(
            content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
        )
        return base64.b64encode(signature).decode("ascii")

    def _common_params(self, method: str) -> dict[str, str]:
        return {
            "app_id": self.app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now(_BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
        }

    async def create_payment(self, order) -> PaymentIntent:
        params = self._common_params("alipay.trade.page.pay")
        params.update(
            {
                "notify_url": self.notify_url,
                "return_url": self.return_url,
                "biz_content": json.dumps(
                    {
                        "out_trade_no": order.order_no,
                        "product_code": "FAST_INSTANT_TRADE_PAY",
                        "total_amount": f"{order.amount_cents / 100:.2f}",
                        "subject": order.product_name[:200],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
        params["sign"] = self._sign(params)
        return PaymentIntent(
            pay_url=f"{self.gateway}?{urlencode(params)}",
            provider_payload={"gateway": self.gateway},
        )

    async def query_order(self, order) -> PaymentNotification | None:
        params = self._common_params("alipay.trade.query")
        params["biz_content"] = json.dumps(
            {"out_trade_no": order.order_no}, ensure_ascii=False, separators=(",", ":")
        )
        params["sign"] = self._sign(params)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(self.gateway, params=params)
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"支付宝查单失败: {type(exc).__name__}")
            return None
        body = data.get("alipay_trade_query_response") or {}
        if body.get("code") != "10000":
            return None
        trade_status = body.get("trade_status")
        if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            return PaymentNotification(
                order_no=order.order_no,
                trade_no=body.get("trade_no"),
                amount_cents=_amount_to_cents(body.get("total_amount")),
                success=True,
                raw={"trade_status": trade_status, "trade_no": body.get("trade_no")},
            )
        if trade_status == "TRADE_CLOSED":
            return PaymentNotification(
                order_no=order.order_no,
                trade_no=None,
                amount_cents=None,
                success=False,
                raw={"trade_status": trade_status},
            )
        return None

    async def parse_notify(
        self,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
    ) -> PaymentNotification:
        data = dict(form or {})
        signature = data.pop("sign", "")
        data.pop("sign_type", None)
        content = "&".join(
            f"{key}={data[key]}"
            for key in sorted(data)
            if data[key] not in (None, "")
        )
        try:
            self._public_key.verify(
                base64.b64decode(signature),
                content.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise PaymentError("支付宝回调验签失败") from exc

        if data.get("app_id") != self.app_id:
            raise PaymentError("支付宝回调 AppID 不匹配")
        trade_status = data.get("trade_status")
        success = trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED")
        return PaymentNotification(
            order_no=str(data.get("out_trade_no") or ""),
            trade_no=str(data.get("trade_no") or "") or None,
            amount_cents=_amount_to_cents(data.get("total_amount")),
            success=success,
            raw={"trade_status": trade_status, "trade_no": data.get("trade_no")},
        )
