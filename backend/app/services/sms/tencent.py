"""腾讯云短信（SendSms 2021-01-11）——TC3-HMAC-SHA256 直连实现。

不引 tencentcloud-sdk-python（单包体积大、依赖多）；短信只用一个 Action，
签名与请求体都很小，直接 httpx 更轻。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
from loguru import logger

from app.services.integration_settings_service import IntegrationConfig
from app.services.sms.base import SmsError, SmsProvider
from app.utils.verification import mask_account

_HOST = "sms.tencentcloudapi.com"
_SERVICE = "sms"
_VERSION = "2021-01-11"
_ACTION = "SendSms"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(msg: str) -> str:
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


def _template_params(raw: str, code: str, ttl_minutes: int) -> list[str]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, list) or not data:
        data = ["{code}", "5"]
    return [
        str(item).replace("{code}", code).replace("{minutes}", str(ttl_minutes))
        for item in data
    ]


class TencentSmsProvider(SmsProvider):
    name = "tencent"

    def __init__(self, config: IntegrationConfig):
        self.secret_id = config.secret("sms_tencent_secret_id")
        self.secret_key = config.secret("sms_tencent_secret_key")
        self.sdk_app_id = config.str("sms_tencent_sdk_app_id")
        self.sign_name = config.str("sms_tencent_sign_name")
        self.template_id = config.str("sms_tencent_template_id")
        self.region = config.str("sms_tencent_region") or "ap-guangzhou"
        self.template_params = config.str("sms_tencent_template_params")
        if not all(
            (self.secret_id, self.secret_key, self.sdk_app_id, self.sign_name, self.template_id)
        ):
            raise SmsError("腾讯云短信参数不完整（SecretId/SecretKey/SDKAppID/签名/模板 ID）")

    async def send_code(self, phone: str, code: str, *, ttl_minutes: int = 5) -> None:
        payload = json.dumps(
            {
                "PhoneNumberSet": [f"+86{phone}"],
                "SmsSdkAppId": self.sdk_app_id,
                "SignName": self.sign_name,
                "TemplateId": self.template_id,
                "TemplateParamSet": _template_params(self.template_params, code, ttl_minutes),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        timestamp = int(time.time())
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        content_type = "application/json; charset=utf-8"

        canonical_headers = f"content-type:{content_type}\nhost:{_HOST}\n"
        signed_headers = "content-type;host"
        canonical_request = (
            "POST\n/\n\n"
            + canonical_headers
            + "\n"
            + signed_headers
            + "\n"
            + _sha256_hex(payload)
        )
        credential_scope = f"{date}/{_SERVICE}/tc3_request"
        string_to_sign = (
            "TC3-HMAC-SHA256\n"
            + str(timestamp)
            + "\n"
            + credential_scope
            + "\n"
            + _sha256_hex(canonical_request)
        )
        secret_date = _hmac_sha256(f"TC3{self.secret_key}".encode("utf-8"), date)
        secret_service = _hmac_sha256(secret_date, _SERVICE)
        secret_signing = _hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        authorization = (
            f"TC3-HMAC-SHA256 Credential={self.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        headers = {
            "Authorization": authorization,
            "Content-Type": content_type,
            "Host": _HOST,
            "X-TC-Action": _ACTION,
            "X-TC-Version": _VERSION,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Region": self.region,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"https://{_HOST}", content=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(f"腾讯云短信请求失败（{mask_account(phone)}）: {type(exc).__name__}")
            raise SmsError("短信服务暂时不可用") from exc
        except ValueError as exc:
            raise SmsError("短信服务返回了非 JSON 响应") from exc

        result = data.get("Response") or {}
        error = result.get("Error")
        if error:
            logger.warning(
                f"腾讯云短信拒绝（{mask_account(phone)}）: "
                f"{error.get('Code')} {error.get('Message')}"
            )
            raise SmsError("短信发送失败，请稍后再试")
