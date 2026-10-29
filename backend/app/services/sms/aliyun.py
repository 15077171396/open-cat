"""阿里云短信（SendSms 2017-05-25）——RPC HMAC-SHA1 直连实现。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from loguru import logger

from app.services.integration_settings_service import IntegrationConfig
from app.services.sms.base import SmsError, SmsProvider
from app.utils.verification import mask_account

_HOST = "dysmsapi.aliyuncs.com"
_VERSION = "2017-05-25"


def _percent(value: str) -> str:
    return quote(str(value), safe="~")


def _signature(params: dict[str, str], access_secret: str) -> str:
    canonical = "&".join(
        f"{_percent(key)}={_percent(params[key])}" for key in sorted(params)
    )
    string_to_sign = f"GET&%2F&{_percent(canonical)}"
    digest = hmac.new(
        f"{access_secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class AliyunSmsProvider(SmsProvider):
    name = "aliyun"

    def __init__(self, config: IntegrationConfig):
        self.access_key_id = config.str("sms_aliyun_access_key_id")
        self.access_key_secret = config.secret("sms_aliyun_access_key_secret")
        self.sign_name = config.str("sms_aliyun_sign_name")
        self.template_code = config.str("sms_aliyun_template_code")
        self.region = config.str("sms_aliyun_region") or "cn-hangzhou"
        self.param_key = config.str("sms_aliyun_template_param_key") or "code"
        if not all(
            (self.access_key_id, self.access_key_secret, self.sign_name, self.template_code)
        ):
            raise SmsError("阿里云短信参数不完整（AccessKey/签名/模板 CODE）")

    async def send_code(self, phone: str, code: str, *, ttl_minutes: int = 5) -> None:
        params = {
            "AccessKeyId": self.access_key_id,
            "Action": "SendSms",
            "Format": "JSON",
            "PhoneNumbers": phone,
            "RegionId": self.region,
            "SignName": self.sign_name,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": secrets.token_urlsafe(16),
            "SignatureVersion": "1.0",
            "TemplateCode": self.template_code,
            "TemplateParam": json.dumps({self.param_key: code}, ensure_ascii=False),
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": _VERSION,
        }
        params["Signature"] = _signature(params, self.access_key_secret)
        query = "&".join(f"{_percent(key)}={_percent(value)}" for key, value in params.items())

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"https://{_HOST}/?{query}")
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(f"阿里云短信请求失败（{mask_account(phone)}）: {type(exc).__name__}")
            raise SmsError("短信服务暂时不可用") from exc
        except ValueError as exc:
            raise SmsError("短信服务返回了非 JSON 响应") from exc

        if data.get("Code") != "OK":
            logger.warning(
                f"阿里云短信拒绝（{mask_account(phone)}）: "
                f"{data.get('Code')} {data.get('Message')}"
            )
            raise SmsError("短信发送失败，请稍后再试")
