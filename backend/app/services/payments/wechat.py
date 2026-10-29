"""微信支付 v3 Native（扫码）——商户私钥签名 + 平台公钥验签 + APIv3 密钥解密。"""

from __future__ import annotations

import base64
import json
import secrets
import time
from binascii import Error as BinasciiError
from typing import Mapping

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from loguru import logger

from app.services.integration_settings_service import IntegrationConfig
from app.services.payments.base import (
    PaymentError,
    PaymentIntent,
    PaymentNotification,
    PaymentProvider,
)

_API_BASE = "https://api.mch.weixin.qq.com"
_NATIVE_PATH = "/v3/pay/transactions/native"


class WechatPayProvider(PaymentProvider):
    channel = "wechat"

    def __init__(self, config: IntegrationConfig):
        self.config = config
        self.mch_id = config.str("wechat_pay_mch_id")
        self.app_id = config.str("wechat_pay_appid")
        self.serial_no = config.str("wechat_pay_serial_no")
        self.api_v3_key = config.secret("wechat_pay_api_v3_key")
        self.private_key_pem = config.secret("wechat_pay_private_key")
        self.platform_public_key_pem = config.str("wechat_pay_platform_public_key")
        self.notify_url = config.str("wechat_pay_notify_url") or (
            f"{config.site_base_url}/api/v1/payments/notify/wechat"
        )
        missing = [
            name
            for name, value in (
                ("商户号", self.mch_id),
                ("AppID", self.app_id),
                ("证书序列号", self.serial_no),
                ("APIv3 密钥", self.api_v3_key),
                ("商户私钥", self.private_key_pem),
                ("平台公钥", self.platform_public_key_pem),
            )
            if not value
        ]
        if missing:
            raise PaymentError(f"微信支付参数不完整：{'、'.join(missing)}")
        if len(self.api_v3_key.encode("utf-8")) != 32:
            raise PaymentError("微信支付 APIv3 密钥必须是 32 字节")
        try:
            self._private_key = serialization.load_pem_private_key(
                self.private_key_pem.encode("utf-8"), password=None
            )
        except (ValueError, TypeError) as exc:
            raise PaymentError("微信支付商户私钥格式不正确（需要 PEM）") from exc
        try:
            self._platform_public_key = serialization.load_pem_public_key(
                self.platform_public_key_pem.encode("utf-8")
            )
        except ValueError:
            try:
                self._platform_public_key = x509.load_pem_x509_certificate(
                    self.platform_public_key_pem.encode("utf-8")
                ).public_key()
            except ValueError as exc:
                raise PaymentError("微信支付平台公钥/证书格式不正确（需要 PEM）") from exc

    def _authorization(self, method: str, path: str, body: str) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n"
        signature = base64.b64encode(
            self._private_key.sign(
                message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
            )
        ).decode("ascii")
        return {
            "Authorization": (
                'WECHATPAY2-SHA256-RSA2048 '
                f'mchid="{self.mch_id}",nonce_str="{nonce}",signature="{signature}",'
                f'timestamp="{timestamp}",serial_no="{self.serial_no}"'
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def create_payment(self, order) -> PaymentIntent:
        body = json.dumps(
            {
                "appid": self.app_id,
                "mchid": self.mch_id,
                "description": order.product_name[:127],
                "out_trade_no": order.order_no,
                "notify_url": self.notify_url,
                "amount": {"total": order.amount_cents, "currency": "CNY"},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        headers = self._authorization("POST", _NATIVE_PATH, body)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{_API_BASE}{_NATIVE_PATH}", content=body, headers=headers
                )
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"微信支付 Native 下单失败: {type(exc).__name__}")
            raise PaymentError("微信支付下单失败，请稍后重试") from exc
        if response.status_code >= 400 or not data.get("code_url"):
            logger.warning(
                f"微信支付 Native 下单被拒绝: status={response.status_code} "
                f"code={data.get('code')} message={data.get('message')}"
            )
            raise PaymentError("微信支付下单失败，请检查商户配置")
        return PaymentIntent(
            qr_content=data["code_url"],
            provider_payload={"code_url": data["code_url"]},
        )

    async def query_order(self, order) -> PaymentNotification | None:
        path = (
            f"/v3/pay/transactions/out-trade-no/{order.order_no}?mchid={self.mch_id}"
        )
        headers = self._authorization("GET", path, "")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(f"{_API_BASE}{path}", headers=headers)
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"微信支付查单失败: {type(exc).__name__}")
            return None
        trade_state = data.get("trade_state")
        if trade_state == "SUCCESS":
            amount = (data.get("amount") or {}).get("total")
            return PaymentNotification(
                order_no=order.order_no,
                trade_no=data.get("transaction_id"),
                amount_cents=int(amount) if amount is not None else None,
                success=True,
                raw={"trade_state": trade_state, "transaction_id": data.get("transaction_id")},
            )
        if trade_state in ("CLOSED", "REVOKED", "PAYERROR"):
            return PaymentNotification(
                order_no=order.order_no,
                trade_no=None,
                amount_cents=None,
                success=False,
                raw={"trade_state": trade_state},
            )
        return None

    async def parse_notify(
        self,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
    ) -> PaymentNotification:
        body = body or b""
        headers = headers or {}
        timestamp = headers.get("Wechatpay-Timestamp") or headers.get("wechatpay-timestamp")
        nonce = headers.get("Wechatpay-Nonce") or headers.get("wechatpay-nonce")
        signature_b64 = headers.get("Wechatpay-Signature") or headers.get("wechatpay-signature")
        if not (timestamp and nonce and signature_b64):
            raise PaymentError("微信支付回调缺少签名头")
        message = f"{timestamp}\n{nonce}\n{body.decode('utf-8')}\n"
        try:
            self._platform_public_key.verify(
                base64.b64decode(signature_b64),
                message.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, BinasciiError, ValueError, TypeError) as exc:
            raise PaymentError("微信支付回调验签失败") from exc

        try:
            envelope = json.loads(body or b"{}")
            resource = envelope.get("resource") or {}
            plaintext = AESGCM(self.api_v3_key.encode("utf-8")).decrypt(
                str(resource.get("nonce") or "").encode("utf-8"),
                base64.b64decode(resource.get("ciphertext") or ""),
                str(resource.get("associated_data") or "").encode("utf-8"),
            )
            data = json.loads(plaintext)
        except (InvalidTag, BinasciiError, ValueError, TypeError) as exc:
            raise PaymentError("微信支付回调解密失败") from exc

        if data.get("mchid") != self.mch_id or data.get("appid") != self.app_id:
            raise PaymentError("微信支付回调商户/AppID 不匹配")
        amount = (data.get("amount") or {}).get("total")
        return PaymentNotification(
            order_no=str(data.get("out_trade_no") or ""),
            trade_no=str(data.get("transaction_id") or "") or None,
            amount_cents=int(amount) if amount is not None else None,
            success=data.get("trade_state") == "SUCCESS",
            raw={
                "trade_state": data.get("trade_state"),
                "transaction_id": data.get("transaction_id"),
            },
        )
