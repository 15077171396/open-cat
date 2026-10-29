"""TOTP（RFC 6238）实现 —— **零第三方依赖**。

## 为什么自己实现而不是装 pyotp

1. **少一个供应链面**：本项目已有 4 份 npm lockfile + 1 份 pip 清单，
   依赖越少越不容易因为上游 CVE 被动升级（管理端二次验证恰恰是安全边界）。
2. **RFC 6238 只有三十行**：HOTP = HMAC-SHA1 + 动态截断，标准测试向量是公开的，
   可以逐条对齐（见 `tests/test_totp.py` 的 RFC 附录 B 用例）。
3. 自己写就能把「时钟偏移窗口」「恢复码哈希」这些**业务口径**写在一处并单测。

## 安全约定（改之前先读）

- 校验用 `hmac.compare_digest`（常数时间），不用 `==` —— 后者会泄漏前缀匹配长度。
- 允许 ±1 个时间步（30s）的时钟偏移：手机与服务器的时间几乎不可能完全同步，
  窗口取 0 会让一部分用户永远登录失败；取更大（±2 以上）则显著放大爆破空间。
- 恢复码**不存明文**，只存 `sha256(user_id:code)`。恢复码是一次性高熵随机串
  （50 bit），不加盐的 sha256 足以防「拖库后直接看到可用码」。
- 密钥由调用方用 `core/crypto.py` 加密后再落库（本模块只处理明文，不碰存储）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote

from app.core.degrade import degrade_guard

# 时间步长（秒）与位数：与 Google Authenticator / 1Password 等默认值一致，
# 改了会导致已绑定的 App 全部失效
STEP_SECONDS = 30
DIGITS = 6

# 允许的时钟偏移步数（±1 步 = ±30 秒）
DEFAULT_WINDOW = 1

# 恢复码：10 个、每个 10 位（去掉易混淆的 0/O/1/I）
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_RECOVERY_COUNT = 10
_RECOVERY_LENGTH = 10

# 密钥长度：20 字节 = 160 bit，RFC 4226 推荐值，Base32 后 32 字符
SECRET_BYTES = 20


def _b32decode(secret: str) -> bytes:
    """Base32 解码（容忍小写、空格、连字符与缺失的 padding）。

    用户可能从 App 里手工抄密钥，抄进来的字符串常带空格或小写；
    这里做归一化而不是直接报错 —— 报错只会让用户卡在绑定流程里。
    """
    cleaned = "".join(str(secret).split()).replace("-", "").upper()
    padding = (-len(cleaned)) % 8
    return base64.b32decode(cleaned + "=" * padding)


def generate_secret(num_bytes: int = SECRET_BYTES) -> str:
    """生成新密钥（Base32，无 padding）"""
    return base64.b32encode(secrets.token_bytes(num_bytes)).decode("ascii").rstrip("=")


def _hotp(key: bytes, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 HOTP：HMAC-SHA1 → 动态截断 → 取模"""
    message = counter.to_bytes(8, "big")
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def totp_code(
    secret: str, at: float | None = None, step: int = STEP_SECONDS, digits: int = DIGITS
) -> str:
    """计算某一时刻的验证码（`at` 为 Unix 秒；不传取当前时间）"""
    timestamp = time.time() if at is None else float(at)
    counter = int(timestamp // step)
    return _hotp(_b32decode(secret), counter, digits)


def verify_totp(
    secret: str,
    code: str | None,
    *,
    at: float | None = None,
    step: int = STEP_SECONDS,
    digits: int = DIGITS,
    window: int = DEFAULT_WINDOW,
) -> bool:
    """校验验证码；`window` 为允许的偏移步数（±window）。

    任何异常（密钥非法、code 非数字）都返回 False —— 校验函数不该把
    「用户输入不合法」升级成 500。
    """
    if not secret or not code:
        return False
    candidate = str(code).strip().replace(" ", "")
    if len(candidate) != digits or not candidate.isdigit():
        return False
    timestamp = time.time() if at is None else float(at)
    counter = int(timestamp // step)
    # 密钥损坏等价于「验证码不对」：不能抛 500，也不能泄露细节
    key = None
    with degrade_guard("totp.decode_secret", detail="TOTP 密钥无法解码，按校验失败处理"):
        key = _b32decode(secret)
    if key is None:
        return False
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_hotp(key, counter + offset, digits), candidate):
            return True
    return False


def provisioning_uri(secret: str, account: str, issuer: str = "AI学习猫") -> str:
    """生成 otpauth:// URI（扫码即绑定）。

    参数顺序与编码方式按 Key Uri Format 规范：`issuer` 同时出现在 label 前缀
    与 query 里，少一处会让部分 App 显示成「未命名账户」。
    """
    label = f"{quote(issuer, safe='')}:{quote(str(account), safe='')}"
    query = (
        f"secret={secret}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )
    return f"otpauth://totp/{label}?{query}"


def normalize_recovery_code(code: str | None) -> str:
    """归一化恢复码：去空格/连字符、转大写（用户常带连字符抄写）"""
    return "".join(str(code or "").split()).replace("-", "").upper()


def generate_recovery_codes(
    count: int = _RECOVERY_COUNT, length: int = _RECOVERY_LENGTH
) -> list[str]:
    """生成一次性恢复码（形如 `ABCDE-FGHJK`），**只在绑定成功时返回一次**"""
    codes: list[str] = []
    for _ in range(max(1, count)):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(max(4, length)))
        half = len(raw) // 2
        codes.append(f"{raw[:half]}-{raw[half:]}")
    return codes


def hash_recovery_code(code: str, user_id: int) -> str:
    """恢复码哈希（不存明文）。

    绑定 user_id 作为前缀盐：同一串码在另一个账号下哈希不同，
    避免「撞码」被跨账号利用。
    """
    normalized = normalize_recovery_code(code)
    return hashlib.sha256(f"{user_id}:{normalized}".encode("utf-8")).hexdigest()
