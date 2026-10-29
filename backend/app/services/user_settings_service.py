"""用户设置服务 - 个人资料、密码、绑定、通知偏好、注销"""

import json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.user import User
from app.models.announcement import NotificationSetting
from app.core.security import (
    verify_password_async,
    hash_password_async,
    validate_password_length,
    bump_token_version,
)
from app.core.exceptions import AppException
from app.redis import redis_client, log_redis_degraded
from app.services import moderation_service
from app.utils.sanitize import sanitize_plain_text
from app.utils.verification import verify_code, mask_account
from app.utils.verification import is_email, is_phone


async def _safe_cache_delete(*keys: str) -> None:
    """删除 Redis key（commit 后的非关键清理：失败仅记日志，
    不向客户端报「实际已成功」的误导性 500）"""
    try:
        await redis_client.delete(*keys)
    except Exception:
        log_redis_degraded("user_settings.invalidate", f"清除缓存失败（Redis 不可用）: {keys}")


async def _safe_cache_get(key: str) -> str | None:
    """读取 Redis 缓存。

    缓存只是加速层：Redis 不可用时返回 None，让调用方**直查数据库**，
    绝不能把「缓存故障」放大成接口 500（与全项目降级口径一致）。
    """
    try:
        return await redis_client.get(key)
    except Exception as exc:
        log_redis_degraded("user_settings_service.cache_get", exc)
        return None


async def _safe_cache_set(key: str, ttl: int, value: str) -> None:
    """写入 Redis 缓存（非关键路径：失败仅记降级日志，不影响本次返回）"""
    try:
        await redis_client.set(key, value, ex=ttl)
    except Exception as exc:
        log_redis_degraded("user_settings_service.cache_set", exc)


# 个人资料


async def _screen_ugc(
    db: AsyncSession, user: User, *, content_type: str, text: str, reject_message: str
) -> str:
    """UGC 文本敏感词筛查，返回可用文本；命中且处置为 reject 时抛 400。

    命中一律留痕（含 mask / review 这类放行处置），管理员可在审核队列复查
    机器判定是否为误报。
    """
    result = moderation_service.screen_text(text)
    if not result.hits:
        return text
    await moderation_service.log_hit(
        db, content_type=content_type, user_id=user.id,
        text=text, hits=result.hits, action=result.action, target_id=str(user.id),
    )
    if not result.passed:
        await db.commit()
        raise AppException(code=400, message=reject_message)
    return result.text

async def update_profile(db: AsyncSession, user: User, nickname: str = None, bio: str = None) -> dict:
    """更新个人资料

    昵称与简介是**公开可见**的 UGC（列表页作者署名位直接渲染），风险高于
    反馈正文。此前两者都不过 sanitize、不过敏感词，属台账 P0-5 的缺口。
    """
    if nickname is not None:
        nickname = nickname.strip()
        if not nickname:
            raise AppException(code=400, message="昵称不能为空")
        if len(nickname) > 50:
            raise AppException(code=400, message="昵称最长 50 个字符")
        # 纯文本字段：不保留任何 HTML 标签（与反馈联系方式同口径）
        nickname = sanitize_plain_text(nickname)
        nickname = await _screen_ugc(
            db, user, content_type="nickname", text=nickname,
            reject_message="昵称包含不当信息，请修改后重试",
        )
        user.nickname = nickname
    if bio is not None:
        bio = (bio or "").strip()
        if len(bio) > 500:
            raise AppException(code=400, message="个人简介最长 500 个字符")
        bio = sanitize_plain_text(bio)
        bio = await _screen_ugc(
            db, user, content_type="bio", text=bio,
            reject_message="个人简介包含不当信息，请修改后重试",
        )
        user.bio = bio or None
    await db.commit()
    await _safe_cache_delete(f"user_stats:{user.id}", f"user_level:{user.id}")
    return {
        "id": user.id,
        "account": user.account,
        "nickname": user.nickname,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
    }


async def update_avatar(db: AsyncSession, user: User, avatar_url: str) -> dict:
    """更新头像 URL"""
    if not avatar_url:
        raise AppException(code=400, message="头像 URL 不能为空")
    user.avatar_url = avatar_url
    await db.commit()
    return {"avatar_url": user.avatar_url}


# 密码

async def change_password(
    db: AsyncSession, user: User, old_password: str, new_password: str
) -> None:
    """修改密码"""
    if not old_password or not new_password:
        raise AppException(code=400, message="请输入完整密码")
    if len(new_password) < 6:
        raise AppException(code=400, message="新密码至少 6 位")
    try:
        validate_password_length(new_password)
    except ValueError as exc:
        raise AppException(code=400, message="Password must not exceed 72 UTF-8 bytes") from exc
    if not await verify_password_async(old_password, user.password_hash):
        raise AppException(code=400, message="当前密码错误")
    user.password_hash = await hash_password_async(new_password)
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    # 修改密码后使 refresh_token 失效，强制重新登录；清除统计缓存
    # （commit 后的非关键清理，失败仅记日志）
    await _safe_cache_delete(
        f"refresh_token:{user.id}", f"user_stats:{user.id}", f"user_level:{user.id}"
    )
    # 递增令牌版本号：吊销全部存量 access_token（防盗号者在改密后 2 小时内继续使用旧 token）
    await bump_token_version(user.id)
    logger.info(f"用户 {user.id} 修改密码")


# 绑定信息
# 判定口径复用 utils.verification（P2-57 之六：这里曾是账号正则的第 5 份手抄）


async def get_bindings(user: User) -> dict:
    """获取账号绑定信息

    根据 account 字段判断是手机号还是邮箱
    """
    account = user.account or ""
    # 手机号与 account 已解耦：邮箱账号也可以绑定手机号；历史「account 即手机号」仍兜底
    phone = user.phone or (account if is_phone(account) else None)
    masked_phone = phone[:3] + "****" + phone[-4:] if phone else None
    if is_email(account):
        # 邮箱脱敏：ab***@example.com
        name, _, domain = account.partition("@")
        masked_email = (name[:2] + "***@" + domain) if len(name) > 2 else (name + "***@" + domain)
    else:
        masked_email = None
    return {
        "phone": masked_phone,
        "phone_bound": bool(phone),
        "email": masked_email,
        "email_bound": bool(masked_email),
    }


async def change_email(db: AsyncSession, user: User, new_email: str, code: str) -> dict:
    """更换邮箱

    流程：校验邮箱格式 → 验证码校验（验证码发送至新邮箱）→ 唯一性校验 → 更新 account
    """
    new_email = (new_email or "").strip()
    if not is_email(new_email):
        raise AppException(code=400, message="请输入正确的邮箱格式")
    if new_email == user.account:
        raise AppException(code=400, message="新邮箱与当前邮箱一致")

    # 验证码校验（验证码是发送到新邮箱的）
    if not await verify_code(new_email, code):
        raise AppException(code=400, message="验证码错误或已过期")

    # 唯一性校验：新邮箱不能已被其他用户注册
    existing = (
        await db.execute(
            select(User).where(User.account == new_email, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if existing:
        raise AppException(code=400, message="该邮箱已被注册")

    old_account = user.account
    user.account = new_email
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）。
    # SAVEPOINT 语义兜底（与注册侧竞态处理口径一致）：预检查通过后并发换绑/注册
    # 同一邮箱时，后提交者撞 account 唯一约束，降级为幂等 409 而非全局 500
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppException(code=409, message="该邮箱已被注册")
    # 清除用户相关缓存（commit 后的非关键清理，失败仅记日志）
    await _safe_cache_delete(
        f"refresh_token:{user.id}", f"user_stats:{user.id}", f"user_level:{user.id}"
    )
    # 账号身份变更：吊销全部存量 access_token
    await bump_token_version(user.id)
    # 邮箱属 PII，日志统一脱敏 (ARCH-014)
    logger.info(f"用户 {user.id} 更换邮箱: {mask_account(old_account)} -> {mask_account(new_email)}")
    return {"account": user.account}


# 通知设置

async def get_notification_settings(db: AsyncSession, user: User) -> dict:
    """获取通知设置（不存在则创建默认）"""
    setting = (
        await db.execute(
            select(NotificationSetting).where(NotificationSetting.user_id == user.id)
        )
    ).scalar_one_or_none()
    if setting is None:
        setting = NotificationSetting(user_id=user.id)
        db.add(setting)
        # 惰性创建默认设置行需立即提交（get_db teardown 的 commit 晚于响应发送）
        await db.commit()
    return _setting_to_dict(setting)


async def update_notification_settings(db: AsyncSession, user: User, updates: dict) -> dict:
    """更新通知设置"""
    setting = (
        await db.execute(
            select(NotificationSetting).where(NotificationSetting.user_id == user.id)
        )
    ).scalar_one_or_none()
    if setting is None:
        setting = NotificationSetting(user_id=user.id)
        db.add(setting)
    for key in ("system", "comment", "like", "private", "email"):
        if key in updates:
            setattr(setting, key, bool(updates[key]))
    await db.commit()
    return _setting_to_dict(setting)


def _setting_to_dict(setting: NotificationSetting) -> dict:
    return {
        "system": setting.system,
        "comment": setting.comment,
        "like": setting.like,
        "private": setting.private,
        "email": setting.email,
    }


# 注销账号

async def cleanup_personal_data_on_reactivation(db: AsyncSession, user: User) -> None:
    """账号复活（注销后重新注册）时清空上一账号周期的个人数据

    重新注册视为全新账号：删除收藏、学习记录、下载历史、消息、
    工具使用日志与通知设置，并重置昵称/简介/头像。
    注销期间数据保留（供统计审计），复活时才彻底清空。
    """
    from sqlalchemy import delete

    from app.models.favorite import Favorite
    from app.models.learning import LearningRecord
    from app.models.download import Download, ToolUsageLog
    from app.models.message import Message

    user.nickname = None
    user.bio = None
    user.avatar_url = None
    for model in (Favorite, LearningRecord, Download, ToolUsageLog, Message, NotificationSetting):
        await db.execute(delete(model).where(model.user_id == user.id))
    # 个人统计/等级缓存已随注销清理，此处兜底再清一次
    await redis_client.delete(f"user_stats:{user.id}", f"user_level:{user.id}")


async def delete_account(db: AsyncSession, user: User) -> None:
    """注销账号（软删除：标记 deleted_at，保留用户记录与关联数据）

    用户行不删除，仅设置 deleted_at 时间戳；关联的收藏/学习/下载/消息等数据保留。
    登录与鉴权查询需通过 deleted_at IS NULL 过滤已注销用户。
    认证相关：清除待审核标记并删除 pending 认证申请，避免注销后重新注册时
    残留上一周期的认证记录。
    """
    from datetime import datetime, timezone

    from app.services.audit_service import cleanup_auth_data_on_deactivation

    logger.warning(f"用户 {user.id} 申请注销账号")
    user.deleted_at = datetime.now(timezone.utc)
    await cleanup_auth_data_on_deactivation(db, user)
    await db.commit()
    # 注销后清理 refresh_token 与统计缓存（commit 后的非关键清理，失败仅记日志）
    await _safe_cache_delete(
        f"refresh_token:{user.id}", f"user_stats:{user.id}", f"user_level:{user.id}"
    )
    # 注销即吊销全部存量 access_token（deleted_at 过滤已挡后续请求，此处纵深防御）
    await bump_token_version(user.id)


# 用户等级

async def get_user_level(db: AsyncSession, user: User) -> dict:
    """获取用户等级与成就（基于行为数据的简单等级系统）

    等级规则：累计学习时长 / 3600 取整 + 1，封顶 10
    """
    cache_key = f"user_level:{user.id}"
    cached = await _safe_cache_get(cache_key)
    if cached:
        return json.loads(cached)

    from sqlalchemy import func
    from app.models.learning import LearningRecord
    from app.models.favorite import Favorite
    from app.models.download import Download

    # 学习时长（秒）
    used = user.free_used_seconds or 0
    # 学习记录数
    learning_count = (
        await db.execute(
            select(func.count(LearningRecord.id)).where(LearningRecord.user_id == user.id)
        )
    ).scalar() or 0
    # 收藏数
    fav_count = (
        await db.execute(
            select(func.count(Favorite.id)).where(Favorite.user_id == user.id)
        )
    ).scalar() or 0
    # 下载数
    dl_count = (
        await db.execute(
            select(func.count(Download.id)).where(Download.user_id == user.id)
        )
    ).scalar() or 0

    level = min(10, max(1, used // 3600 + 1))

    # 成就标签
    achievements: list[str] = []
    if learning_count >= 5:
        achievements.append("学习达人")
    if fav_count >= 10:
        achievements.append("收藏家")
    if dl_count >= 10:
        achievements.append("资源猎人")
    if user.role == "vip":
        achievements.append("高级会员")
    if not achievements:
        achievements.append("知识探索者")

    level_label = {1: "新手", 2: "初级", 3: "初级", 4: "中级", 5: "高级用户"}.get(level, "资深用户")

    result = {
        "level": level,
        "level_label": f"Lv.{level} {level_label}",
        "achievements": achievements,
        "stats": {
            "learning_seconds": used,
            "learning_count": learning_count,
            "favorites": fav_count,
            "downloads": dl_count,
        },
    }
    await _safe_cache_set(cache_key, 60, json.dumps(result))
    return result


# 用户聚合统计

async def get_user_stats(db: AsyncSession, user: User) -> dict:
    """个人中心顶部 4 个统计数字"""
    cache_key = f"user_stats:{user.id}"
    cached = await _safe_cache_get(cache_key)
    if cached:
        return json.loads(cached)

    from sqlalchemy import func
    from app.models.favorite import Favorite
    from app.models.learning import LearningRecord
    from app.models.download import Download
    from app.models.message import Message

    favorites = (
        await db.execute(
            select(func.count(Favorite.id)).where(Favorite.user_id == user.id)
        )
    ).scalar() or 0
    learning = (
        await db.execute(
            select(func.count(LearningRecord.id)).where(LearningRecord.user_id == user.id)
        )
    ).scalar() or 0
    downloads = (
        await db.execute(
            select(func.count(Download.id)).where(Download.user_id == user.id)
        )
    ).scalar() or 0
    messages = (
        await db.execute(
            select(func.count(Message.id)).where(Message.user_id == user.id)
        )
    ).scalar() or 0
    result = {
        "favorites": favorites,
        "learning": learning,
        "downloads": downloads,
        "messages": messages,
    }
    await _safe_cache_set(cache_key, 60, json.dumps(result))
    return result
