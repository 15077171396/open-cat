"""验证码服务 - 短信/邮件验证码"""

import secrets
import string
import re
from email.message import EmailMessage
from loguru import logger
from redis.exceptions import ResponseError

from app.redis import redis_client
from app.config import settings
from app.utils.mailer import connect_kwargs

# 验证码 Redis key 前缀
CODE_PREFIX = "verify_code:"
# 验证码有效期 (秒)
CODE_EXPIRE = 300  # 5 分钟
# 发送间隔限制 (秒)
SEND_INTERVAL = 60
# 验证码校验失败最大次数
MAX_VERIFY_ATTEMPTS = 5
# 校验失败锁定时长 (秒)
VERIFY_LOCK_SECONDS = 600


# 账号格式（手机号/邮箱）全仓唯一定义点，schemas/auth.py 与 api/admin_users.py 均应复用 is_phone/is_email；
# 正则刻意加 re.I，邮箱域名的 ci 语义与 MySQL utf8mb4_unicode_ci 一致
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$", re.I)

ACCOUNT_FORMAT_ERROR = "请输入正确的手机号或邮箱"


def is_phone(account: str) -> bool:
    """是否为手机号"""
    return bool(_PHONE_RE.match(account))


def is_email(account: str) -> bool:
    """是否为邮箱"""
    return bool(_EMAIL_RE.match(account))


def is_email_or_phone(account: str) -> bool:
    """注册 / 发送验证码 / 后台建号三条入口共用的账号口径。"""
    return is_phone(account) or is_email(account)


def mask_account(account: str) -> str:
    """对账号脱敏：手机号 138****1234，邮箱 a***@example.com"""
    if is_phone(account):
        return account[:3] + "****" + account[-4:]
    if is_email(account):
        name, _, domain = account.partition("@")
        return (name[:1] + "***@" + domain) if name else account
    return account


def generate_code() -> str:
    """生成 6 位数字验证码 (密码学安全)"""
    return ''.join(secrets.choice(string.digits) for _ in range(6))


async def send_code(account: str) -> dict:
    """发送验证码，返回 {"success", "message", "status"}；status 为建议 HTTP 状态码（400 格式错误 / 429 频控 / 502 发送失败）"""
    # 先校验账号格式：避免无效账号占用 60 秒频控锁
    if not (is_phone(account) or is_email(account)):
        return {"success": False, "message": "账号格式不正确", "status": 400}

    # 原子设置发送间隔 (SET NX)：并发请求只有一个能通过，替代 exists+setex 的竞态窗口
    interval_key = f"{CODE_PREFIX}interval:{account}"
    if not await redis_client.set(interval_key, "1", nx=True, ex=SEND_INTERVAL):
        ttl = await redis_client.ttl(interval_key)
        # 竞态下 key 恰好过期时 ttl 返回 -1/-2，钳制为 0 避免出现"请 -2 秒后再试"
        ttl = max(ttl, 0)
        return {"success": False, "message": f"发送过于频繁，请 {ttl} 秒后再试", "status": 429}

    code = generate_code()

    if is_phone(account):
        result = await send_sms(account, code)
    else:
        result = await send_email(account, code)

    if not result:
        # 发送失败：回滚频控锁让用户可立即重试，且不残留无效验证码
        await redis_client.delete(interval_key)
        logger.bind(event="verify_code_send_failed", account=mask_account(account)).warning("验证码发送失败")
        return {"success": False, "message": "验证码发送失败", "status": 502}

    # 发送成功后才写入验证码（旧实现先写后发，失败会残留已失效的验证码）
    code_key = f"{CODE_PREFIX}{account}"
    await redis_client.set(code_key, code, ex=CODE_EXPIRE)
    # 新验证码生效时重置失败计数：否则用户输错 5 次锁 10 分钟后重新获取验证码，
    # 第一次输错即达到上限再次锁定，陷入"锁死循环"（也被攻击者用作账号锁定 DoS）
    await redis_client.delete(f"verify_fail:{account}")

    if settings.is_dev:
        # 结构化日志: 账号脱敏, 不记录验证码明文 (ARCH-014)
        logger.bind(event="verify_code_sent", account=mask_account(account), channel="sms" if is_phone(account) else "email").info(
            f"验证码已发送至 {mask_account(account)} (开发环境)"
        )
    return {"success": True, "message": "验证码已发送"}


async def verify_code(account: str, code: str) -> bool:
    """校验验证码"""
    # 锁定检查：连续失败次数达上限后锁定一段时间
    lock_key = f"verify_lock:{account}"
    if await redis_client.exists(lock_key):
        return False

    code_key = f"{CODE_PREFIX}{account}"
    stored_code = await redis_client.get(code_key)
    if not stored_code:
        return False
    # 恒定时间比较（两侧均为 str），防按位爆破的时序侧信道。
    # compare_digest 要求 str 参数为 ASCII：非 ASCII 输入（如中文验证码）会抛
    # TypeError 落入全局 500，此处前置判定直接按验证码不匹配处理
    if not code or not code.isascii():
        mismatch = True
    else:
        mismatch = not secrets.compare_digest(stored_code, code)
    if mismatch:
        # 失败计数（验证码保留，用户可继续尝试直至锁定）
        fail_key = f"verify_fail:{account}"
        fail_count = await redis_client.incr(fail_key)
        await redis_client.expire(fail_key, VERIFY_LOCK_SECONDS)
        if fail_count >= MAX_VERIFY_ATTEMPTS:
            await redis_client.delete(code_key)
            await redis_client.set(lock_key, "1", ex=VERIFY_LOCK_SECONDS)
        return False
    # 比对通过：GETDEL 原子消费（Redis >= 6.2）。原先 GET 比较 + DELETE 两步间的
    # TOCTOU 窗口允许两个并发请求同时通过比较、同一验证码被消费多次；
    # 此处仅当取回值与刚才比对值一致才判定成功——并发请求只有一个能消费
    try:
        consumed = await redis_client.getdel(code_key)
    except ResponseError:
        # Redis < 6.2 无 GETDEL：降级为 MULTI/EXEC 事务内的 GET+DEL，
        # 事务原子性同样消除 TOCTOU 窗口（Redis 2.0+ 均支持）
        pipe = redis_client.pipeline(transaction=True)
        pipe.get(code_key)
        pipe.delete(code_key)
        consumed, _ = await pipe.execute()
    if consumed != stored_code:
        # 极窄竞态：等待期间验证码已被并发请求消费或被重新签发，本次判定失败
        return False
    # 验证成功，删除失败计数
    await redis_client.delete(f"verify_fail:{account}")
    return True


async def send_sms(phone: str, code: str) -> bool:
    """发送短信验证码（旧注册链路复用；登录/绑定走 phone_auth_service 的场景化 key）。"""
    from app.services.sms import SmsError, send_sms_code

    try:
        await send_sms_code(phone, code)
        return True
    except SmsError as exc:
        logger.bind(event="sms_send_failed", phone=mask_account(phone)).warning(
            f"短信发送失败: {exc}"
        )
        return False


def _build_email_html(code: str) -> str:
    """构建验证码邮件 HTML 正文"""
    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;">
    <tr><td align="center">
      <table role="presentation" width="420" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
        <tr>
          <td style="background:linear-gradient(135deg,#6366f1,#4f46e5);padding:24px 32px;">
            <span style="font-size:20px;font-weight:700;color:#ffffff;">AI学习猫</span>
          </td>
        </tr>
        <tr>
          <td style="padding:32px;">
            <h1 style="margin:0 0 8px;font-size:18px;color:#1f2937;">注册验证码</h1>
            <p style="margin:0 0 20px;font-size:14px;color:#6b7280;line-height:1.6;">您正在注册 AI学习猫账号，验证码为：</p>
            <div style="text-align:center;background:#f5f3ff;border-radius:12px;padding:20px;margin-bottom:20px;">
              <span style="font-size:32px;font-weight:700;letter-spacing:8px;color:#4f46e5;">{code}</span>
            </div>
            <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.6;">
              验证码 5 分钟内有效，请勿泄露给他人。<br/>
              如非本人操作，请忽略此邮件。
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 32px;background:#f9fafb;border-top:1px solid #f3f4f6;">
            <p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">此邮件由系统自动发送，请勿回复</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_email(to_email: str, code: str) -> bool:
    """发送邮件验证码"""
    # SMTP 未配置：开发环境打印验证码便于联调，生产环境视为失败
    if not settings.SMTP_HOST:
        if settings.is_dev:
            logger.info(f"[邮件验证码] {mask_account(to_email)} -> {code} (SMTP 未配置, 开发环境直接返回)")
            return True
        logger.warning("SMTP 未配置, 无法发送邮件验证码")
        return False

    from_email = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME

    message = EmailMessage()
    message["Subject"] = f"【AI学习猫】注册验证码 {code}"
    message["From"] = f"{settings.SMTP_SENDER_NAME} <{from_email}>"
    message["To"] = to_email
    message.set_content("您的注册验证码为 " + code + "，5 分钟内有效。", charset="utf-8")
    message.add_alternative(_build_email_html(code), subtype="html", charset="utf-8")

    try:
        import aiosmtplib

        # 连接参数走 utils/mailer 的唯一口径：465 = 隐式 TLS，587/25 = STARTTLS。
        # 此前这里只传 start_tls，于是设置项文案写着「465 端口关闭 STARTTLS」，
        # 实际却按明文连 465 端口 —— 后台填 465 的部署会一直发不出验证码。
        await aiosmtplib.send(message, **connect_kwargs())
        if settings.is_dev:
            logger.info(f"[邮件验证码] 已发送至 {mask_account(to_email)} (开发环境额外打印: {code})")
        return True
    except Exception as e:
        logger.bind(event="email_send_failed", account=mask_account(to_email)).error(f"邮件发送失败: {e}")
        return False
