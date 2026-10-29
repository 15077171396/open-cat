"""对称加密工具：把第三方密钥（AI API Key 等）加密后落库。

用 SECRET_KEY 经 PBKDF2 派生密钥（不新增配置项）；轮换 SECRET_KEY 后解密失败返回 None
（调用方回退环境变量，宁可降级不可崩）；密文带 enc:vN: 前缀区分 v1（历史格式，仅解密）与 v2（新写入），
升级时已落库密文仍可读、零破坏迁移。
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from app.config import settings

# 密文格式版本
_PREFIX_V1 = "enc:v1:"
_PREFIX = "enc:v2:"

# 固定应用盐：**非机密**（写死在代码里即可），作用是隔离跨应用的彩虹表，
# 并保证同一 SECRET_KEY 在本应用中派生出唯一的加密密钥。
_KDF_SALT = b"ai-cat::secret-kdf::v2"
_KDF_ITERATIONS = 200_000

# 管理端回显用的掩码前缀。后端把它当作「未修改」的哨兵：
# 前端展示掩码、用户没动这个字段时即使误提交掩码值，也不会把掩码写进库里。
MASK_PREFIX = "********"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """v2 派生（当前写入格式）：PBKDF2 让 SECRET_KEY 熵不足时也无法低成本枚举；
    lru_cache 让昂贵的派生只发生一次（SECRET_KEY 在进程生命周期内不变）"""
    key = hashlib.pbkdf2_hmac(
        "sha256",
        settings.SECRET_KEY.encode("utf-8"),
        _KDF_SALT,
        _KDF_ITERATIONS,
        dklen=32,
    )
    return Fernet(base64.urlsafe_b64encode(key))


@lru_cache(maxsize=1)
def _fernet_legacy() -> Fernet:
    """v1 派生（历史格式）：**仅用于解密**升级前写入的密文，不再用于加密"""
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def is_encrypted(value: str | None) -> bool:
    """是否为本站加密产物（含 v1 / v2 两个版本，用于区分历史明文值）"""
    return bool(value) and (value.startswith(_PREFIX) or value.startswith(_PREFIX_V1))


def encrypt_secret(plain: str) -> str:
    """加密明文。空串原样返回（空 = 未配置，无需加密）"""
    if not plain:
        return ""
    token = _fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_secret(cipher: str | None) -> str | None:
    """解密。失败（SECRET_KEY 已轮换 / 数据损坏）返回 None，由调用方回退"""
    if not cipher:
        return None
    if not is_encrypted(cipher):
        # 历史明文值（本功能上线前写入的）：原样返回，保证兼容
        return cipher
    # 按前缀选择派生方式：v1 存量密文仍用旧密钥解，保证算法升级零破坏
    if cipher.startswith(_PREFIX_V1):
        fernet, body = _fernet_legacy(), cipher[len(_PREFIX_V1):]
    else:
        fernet, body = _fernet(), cipher[len(_PREFIX):]
    try:
        return fernet.decrypt(body.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        logger.warning(f"密文解密失败（SECRET_KEY 可能已轮换）: {type(exc).__name__}")
        return None


def mask_secret(value: str | None) -> str:
    """生成给管理端回显的掩码：只暴露末 4 位（便于确认当前是哪把钥匙，又不泄露完整凭据）"""
    plain = decrypt_secret(value)
    if not plain:
        return ""
    if len(plain) <= 4:
        return MASK_PREFIX
    return f"{MASK_PREFIX}{plain[-4:]}"


def looks_like_mask(value: str | None) -> bool:
    """判断提交上来的值是否为掩码（即「用户没有修改这个字段」）"""
    return bool(value) and value.startswith(MASK_PREFIX)
