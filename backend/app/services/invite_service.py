"""邀请返利服务：邀请码生成 / 注册绑定发奖 / 邀请统计与明细。

事务语义：bind_invite_and_reward 仅 flush（由 register 统一提交），保证关系+余额+流水原子一致。
防刷：被邀请人唯一约束兜底；发奖用原子 UPDATE（相对值自增）；已注销用户邀请码失效、
复活不重复发奖；邀请码 secrets 随机生成且查库去重。
"""

import secrets

from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.user import User
from app.models.invite_relation import InviteRelation
from app.services.point_service import record_transaction, TYPE_INVITE
from app.services.setting_service import get_public_settings
from app.utils.verification import mask_account

# 邀请码字符集：去除易混淆字符 I L O 0 1，8 位
_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_INVITE_CODE_LEN = 8

# 奖励积分上下限（管理员配置异常时钳制到安全区间）
_MAX_REWARD_POINTS = 1_000_000

# 设置默认值（与 SETTING_DEFINITIONS 保持一致）
_DEFAULT_ENABLED = True
_DEFAULT_REWARD = 50


async def get_invite_settings() -> tuple[bool, int]:
    """读取邀请返利配置（功能开关 + 每人奖励积分），走公共设置缓存"""
    try:
        settings = await get_public_settings()
        enabled = str(settings.get("invite_enabled", "1")).strip() == "1"
        try:
            reward = int(str(settings.get("invite_reward_points", str(_DEFAULT_REWARD))).strip())
        except ValueError:
            reward = _DEFAULT_REWARD
        reward = max(0, min(reward, _MAX_REWARD_POINTS))
        return enabled, reward
    except Exception:
        # 设置服务异常不阻断注册主流程：回退默认值
        logger.warning("读取邀请设置失败，回退默认配置")
        return _DEFAULT_ENABLED, _DEFAULT_REWARD


def _generate_invite_code() -> str:
    """生成 8 位随机邀请码（secrets 密码学安全随机）"""
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(_INVITE_CODE_LEN))


async def get_or_create_invite_code(db: AsyncSession, user: User) -> str:
    """获取用户专属邀请码，无则生成（惰性生成：存量用户首次访问邀请页时补发）。

    注意：生成成功时会提交整个会话（当前唯一调用入口 GET /invite/info 前置
    均为只读操作，无副作用）；后续若在本函数之前挂起未提交的业务写入，
    需改为独立 session 完成生成，避免提前固化部分状态。
    """
    if user.invite_code:
        return user.invite_code

    # 撞库重试（唯一索引兜底，防御性上限防死循环）
    for _ in range(10):
        candidate = _generate_invite_code()
        existed = (
            await db.execute(select(User.id).where(User.invite_code == candidate))
        ).first()
        if existed:
            continue
        # savepoint 包裹：极小概率并发撞唯一索引时仅回滚本次赋值，
        # 不污染外层事务（回滚后 ORM 对象属性自动失效，可安全重试）
        try:
            async with db.begin_nested():
                user.invite_code = candidate
                await db.flush()
        except IntegrityError:
            continue
        # 惰性生成的邀请码立即提交（get_db teardown 的 commit 晚于响应发送）
        await db.commit()
        return candidate
    raise RuntimeError("邀请码生成失败，请稍后重试")


async def find_inviter_by_code(db: AsyncSession, code_str: str) -> User | None:
    """按邀请码查找邀请人（未注销的有效用户）；找不到/已注销返回 None"""
    code = (code_str or "").strip().upper()
    if not code:
        return None
    result = await db.execute(
        select(User).where(User.invite_code == code, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def bind_invite_and_reward(db: AsyncSession, inviter: User, invitee: User, reward: int) -> None:
    """新用户注册成功后绑定邀请关系并给邀请人发奖（仅被全新注册调用）。

    事务由调用方（get_db）统一提交：关系、余额、流水要么全成功要么全回滚。
    """
    invitee.referrer_id = inviter.id
    try:
        async with db.begin_nested():
            db.add(InviteRelation(
                inviter_id=inviter.id,
                invitee_id=invitee.id,
                reward_points=reward,
            ))
            await db.flush()
    except IntegrityError:
        # invitee_id 唯一约束兜底：正常不可能触发（invitee 为本事务新建用户），
        # 触发则说明该用户已被绑定过，静默跳过不重复发奖
        logger.warning(f"邀请关系重复绑定被拦截: inviter={inviter.id} invitee={invitee.id}")
        invitee.referrer_id = None
        return

    if reward > 0:
        # 原子加积分（相对值自增，并发下不丢失更新），随后回读真实余额写流水
        await db.execute(
            update(User)
            .where(User.id == inviter.id)
            .values(points=func.coalesce(User.points, 0) + reward)
            .execution_options(synchronize_session=False)
        )
        await db.refresh(inviter)
        record_transaction(
            db,
            user_id=inviter.id,
            points=reward,
            balance_after=inviter.points or 0,
            type=TYPE_INVITE,
            description=f"邀请好友注册奖励：{mask_account(invitee.account)}",
        )
    logger.info(f"邀请注册成功: inviter={inviter.id} invitee={invitee.id} reward={reward}")


async def get_invite_info(db: AsyncSession, user: User) -> dict:
    """我的邀请信息：邀请码 + 已邀请人数 + 累计奖励积分 + 当前配置"""
    enabled, reward = await get_invite_settings()
    invite_code = await get_or_create_invite_code(db, user)
    invitee_count, total_reward = (
        await db.execute(
            select(
                func.count(InviteRelation.id),
                func.coalesce(func.sum(InviteRelation.reward_points), 0),
            ).where(InviteRelation.inviter_id == user.id)
        )
    ).one()
    return {
        "invite_code": invite_code,
        "enabled": enabled,
        "reward_points": reward,
        "invitee_count": invitee_count or 0,
        "total_reward_points": int(total_reward or 0),
    }


async def list_invitees(
    db: AsyncSession, user: User, page: int = 1, page_size: int = 10,
) -> dict:
    """我邀请的用户明细（时间倒序，账号脱敏展示）"""
    conditions = [InviteRelation.inviter_id == user.id]
    total = (
        await db.execute(
            select(func.count(InviteRelation.id)).where(*conditions)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(InviteRelation, User.account)
            .join(User, User.id == InviteRelation.invitee_id)
            .where(*conditions)
            .order_by(InviteRelation.created_at.desc(), InviteRelation.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        {
            "id": rel.id,
            "invitee_account": mask_account(account) if account else "-",
            "reward_points": rel.reward_points or 0,
            "created_at": rel.created_at.isoformat() if rel.created_at else None,
        }
        for rel, account in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
