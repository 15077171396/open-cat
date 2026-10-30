"""TOTP 二次验证的绑定 / 启用 / 关闭 / 登录校验。

## 为什么后台需要第二道门

后台能改全站内容、看全量手机号邮箱、改积分、批删账号 —— 而此前**只凭一个密码**。
密码泄露（撞库、复用、钓鱼）没有任何兜底。TOTP 是成本最低、对运营负担最小的第二因子
（手机 App 离线算码，不依赖短信通道，也不用采购）。

## 三条安全约定（改之前先读）

1. **密钥加密落库**：`core/crypto.py`（SECRET_KEY 派生）。拖库者拿到密文也算不出验证码。
   代价是 `SECRET_KEY` 轮换后旧密钥解不开 → 此时**明确报错让人重绑**，不能静默放行。
2. **绑定分两步**（setup → enable）：先写密钥但保持 `enabled=0`，用户用 App 验证一次
   通过才置 1。反过来（先置 1 再验证）一旦密钥没存对，账号就永久登录不进去。
3. **恢复码一次性**：登录时用掉即从列表里删除并提交。没有恢复码的话，
   手机丢了等于账号永久锁死（单机项目没有运维后台可以救）。

## 失败处理

- 验证码错误**计入登录失败锁定**（与密码错误同一计数），防止「密码已泄露后爆破 6 位码」：
  10^6 空间看似大，但 30 秒一换、无锁定的话持续爆破是可行的。
- 校验函数本身不抛异常：非法输入一律 False（不把「用户输错」升级成 500）。
"""

from __future__ import annotations

import hmac

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import totp
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import AppException
from app.core.security import verify_password_async
from app.models.user import User
from app.services import setting_service

# 恢复码剩余不足该数量时提醒重新生成（前端据此显示提示）
RECOVERY_WARN_THRESHOLD = 3


def is_totp_enabled(user: User) -> bool:
    """该账号登录时是否需要二次验证"""
    return bool(getattr(user, "totp_enabled", False))


def totp_status(user: User) -> dict:
    """供个人中心展示的绑定状态（**不返回密钥**）"""
    hashes = user.totp_recovery_hashes or []
    return {
        "enabled": is_totp_enabled(user),
        # 绑定了但未启用 = 上次扫码后没完成验证，可以重新 setup 覆盖
        "pending": bool(user.totp_secret) and not is_totp_enabled(user),
        "recovery_remaining": len(hashes),
        "recovery_should_regenerate": is_totp_enabled(user) and len(hashes) <= RECOVERY_WARN_THRESHOLD,
    }


async def setup_totp(db: AsyncSession, user: User) -> dict:
    """生成新密钥与恢复码（**明文只在这里返回一次**）。

    已启用时拒绝：换绑必须先关闭（避免「随手点一下就换了绑、旧 App 还在算码」）。
    """
    if is_totp_enabled(user):
        raise AppException(code=400, message="二次验证已启用，请先关闭后再重新绑定")

    secret = totp.generate_secret()
    recovery_codes = totp.generate_recovery_codes()

    user.totp_secret = encrypt_secret(secret)
    user.totp_enabled = False
    user.totp_recovery_hashes = [totp.hash_recovery_code(code, user.id) for code in recovery_codes]
    await db.commit()

    logger.bind(event="totp_setup", user_id=user.id).info(f"用户 {user.id} 生成 TOTP 绑定信息（待验证）")
    return {
        "secret": secret,
        "uri": totp.provisioning_uri(secret, user.account),
        # ⚠️ 只此一次：库里只有哈希，丢了只能关闭重绑
        "recovery_codes": recovery_codes,
    }


async def enable_totp(db: AsyncSession, user: User, code: str) -> None:
    """用一次验证码确认「App 真的能算出来」，通过后才真正启用"""
    if is_totp_enabled(user):
        raise AppException(code=400, message="二次验证已启用")
    if not user.totp_secret:
        raise AppException(code=400, message="请先生成绑定信息")

    secret = decrypt_secret(user.totp_secret)
    if not secret:
        # SECRET_KEY 轮换后旧密文解不开：明确要求重绑，绝不静默放行
        raise AppException(code=400, message="密钥已失效（服务端密钥变更），请重新绑定")

    if not totp.verify_totp(secret, code):
        raise AppException(code=400, message="验证码不正确，请确认手机时间是否准确")

    user.totp_enabled = True
    await db.commit()
    logger.bind(event="totp_enabled", user_id=user.id).info(f"用户 {user.id} 启用二次验证")


async def _password_matches(user: User, password: str) -> bool:
    """密码校验（**不抛异常**）。

    历史脏数据里可能有非 bcrypt 格式的 hash（早期迁移/手工改库），passlib 遇到会抛
    `UnknownHashError`。关闭二次验证是安全动作，宁可判成「密码不对」也不能 500 ——
    500 会让用户以为「服务坏了」，反复重试。
    """
    if not password:
        return False
    try:
        return bool(await verify_password_async(password, user.password_hash))
    except Exception as exc:  # noqa: BLE001 哈希格式异常按校验失败处理
        # 这里不是 Redis 降级，用普通 warning 更贴切；关键是**必须留痕**，
        # 否则「所有密码都校验失败」会表现成「用户老是输错密码」而无从排查
        logger.warning(f"[降级-放行] totp.password_verify —— 密码哈希无法校验：{type(exc).__name__}")
        return False


async def enforce_admin_2fa_policy(db: AsyncSession, user: User) -> None:
    """管理员强制 2FA：未绑定 TOTP 的管理员不得登录。

    设置开启前会校验「所有有效管理员都已绑定」，正常不会命中；这里是纵深防御，
    防止设置被手工改库 / 新管理员绕过创建校验后出现「管理员只凭密码进入后台」。
    """
    if user.role != "admin" or is_totp_enabled(user):
        return
    if await setting_service.get_bool_setting(db, "admin_require_2fa"):
        raise AppException(
            code=403,
            message="管理员账号必须开启二次验证；请在个人中心完成 TOTP 绑定后再登录",
        )


async def disable_totp(db: AsyncSession, user: User, password: str) -> None:
    """关闭二次验证（**要求密码**：防止「拿到已登录设备的人」直接摘掉第二因子）"""
    if not is_totp_enabled(user):
        raise AppException(code=400, message="二次验证未启用")
    if user.role == "admin" and await setting_service.get_bool_setting(db, "admin_require_2fa"):
        raise AppException(
            code=400,
            message="管理员强制二次验证已开启，不能关闭；请先由超管关闭强制策略",
        )
    if not await _password_matches(user, password):
        raise AppException(code=400, message="密码不正确")

    user.totp_secret = None
    user.totp_enabled = False
    user.totp_recovery_hashes = None
    await db.commit()
    logger.bind(event="totp_disabled", user_id=user.id).warning(f"用户 {user.id} 关闭二次验证")


async def verify_login_totp(db: AsyncSession, user: User, code: str | None) -> bool:
    """登录时的二次校验：先比 TOTP，再比恢复码（用掉即删除）。

    返回 True/False，**不抛异常**：调用方统一按「验证失败」处理并计入失败锁定。
    """
    if not code:
        return False

    secret = decrypt_secret(user.totp_secret) if user.totp_secret else None
    if secret and totp.verify_totp(secret, code):
        return True

    # 恢复码：命中即从列表里摘掉（一次性），并提交 —— 否则同一串码可以反复用
    normalized = totp.normalize_recovery_code(code)
    if not normalized:
        return False
    target = totp.hash_recovery_code(normalized, user.id)
    hashes = list(user.totp_recovery_hashes or [])
    for stored in hashes:
        if hmac.compare_digest(str(stored), target):
            hashes.remove(stored)
            user.totp_recovery_hashes = hashes
            await db.commit()
            logger.bind(event="totp_recovery_used", user_id=user.id).warning(
                f"用户 {user.id} 使用恢复码登录，剩余 {len(hashes)} 个"
            )
            return True
    return False


async def regenerate_recovery_codes(db: AsyncSession, user: User, code: str) -> list[str]:
    """重新生成恢复码（需当前有效验证码，防止「已登录设备」随便刷掉别人的恢复码）"""
    if not is_totp_enabled(user):
        raise AppException(code=400, message="二次验证未启用")
    secret = decrypt_secret(user.totp_secret) if user.totp_secret else None
    if not secret or not totp.verify_totp(secret, code):
        raise AppException(code=400, message="验证码不正确")

    codes = totp.generate_recovery_codes()
    user.totp_recovery_hashes = [totp.hash_recovery_code(item, user.id) for item in codes]
    await db.commit()
    return codes
