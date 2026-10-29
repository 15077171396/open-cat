"""短信通道适配层（mock / 腾讯云 / 阿里云）。"""

from app.services.sms.factory import get_sms_provider, send_sms_code
from app.services.sms.base import SmsError, SmsProvider

__all__ = ["SmsError", "SmsProvider", "get_sms_provider", "send_sms_code"]
