"""会话管理服务：面向「自己」的设备操作 + 管理端强制下线。

为什么不放在 auth_service：那边的职责是「证明你是谁」，这里是「你已经进来了，
现在要管住哪些设备还能继续用」。分开之后，auth_service 的登录/刷新路径不必
知道审计与后台权限的存在。

审计约定与其它后台写操作一致：`record_admin_action` 只 add 不 commit，
由本模块在同一事务里提交 —— 保证「踢成功了但没留痕」不可能发生。
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import blacklist_jti, bump_token_version
from app.core.session_store import list_sessions, revoke_all_sessions, revoke_session
from app.models.user import User
from app.services.admin_audit_service import record_admin_action


def _iso(ts: int) -> str:
    """epoch 秒 → ISO 8601（UTC）。前端统一按本地时区展示。"""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _present(rows: list[dict], current_sid: str | None) -> list[dict]:
    """补上「是否本机」并转成展示字段（不透出 access_jti / UA 原文）"""
    return [
        {
            "sid": row["sid"],
            "device": row["device"],
            "ip": row["ip"],
            "created_at": _iso(row["created_at"]),
            "last_seen": _iso(row["last_seen"]),
            "current": bool(current_sid and row["sid"] == current_sid),
        }
        for row in rows
    ]


async def list_my_sessions(user_id: int, current_sid: str | None) -> list[dict]:
    """当前管理员自己的登录设备（按最近活跃倒序）"""
    return _present(await list_sessions(user_id), current_sid)


async def revoke_my_session(
    db: AsyncSession, admin: User, sid: str, current_sid: str | None
) -> None:
    """踢掉自己名下的某一个会话。

    三件事同时做，少一件都会留下「看起来踢了其实没踢」的洞：
    1. 删会话记录与该会话的 refresh 键 → 它再也续不了期；
    2. 把它当前的 access_jti 拉黑 → 手上那枚 access_token 立刻失效（否则默认还有 120 分钟）；
    3. 写审计 → 与其它后台写操作同一口径。

    ⚠️ 踢自己当前所在的会话是允许的（等价于登出），前端会顺带清本地令牌。
    """
    record = await revoke_session(admin.id, sid)
    if record is None:
        raise AppException(code=404, message="会话不存在或已失效")

    await blacklist_jti(record.get("access_jti") or "")
    record_admin_action(
        db,
        admin,
        "session_revoke",
        target_type="user",
        target_id=admin.id,
        summary=f"下线登录设备 {record.get('device') or '未知设备'}（{sid[:8]}…）",
        detail={"sid": sid, "device": record.get("device"), "ip": record.get("ip")},
    )
    await db.commit()
    logger.bind(event="session_revoke", user_id=admin.id).info(
        f"用户下线自己的设备: sid={sid[:8]}… self={sid == current_sid}"
    )


async def revoke_my_other_sessions(
    db: AsyncSession, admin: User, current_sid: str | None
) -> int:
    """退出其他设备（保留当前这台）。返回踢掉的会话数。"""
    revoked: list[dict] = []
    for row in await list_sessions(admin.id):
        if current_sid and row["sid"] == current_sid:
            continue
        record = await revoke_session(admin.id, row["sid"])
        if record:
            revoked.append(record)
            await blacklist_jti(record.get("access_jti") or "")

    if revoked:
        record_admin_action(
            db,
            admin,
            "session_revoke",
            target_type="user",
            target_id=admin.id,
            summary=f"退出其他设备（{len(revoked)} 个会话）",
            detail={"revoked": len(revoked), "sids": [row["sid"][:8] for row in revoked]},
        )
        await db.commit()
    return len(revoked)


async def force_logout_user(db: AsyncSession, admin: User, user_id: int) -> int:
    """管理员把某个用户的所有设备踢下线（改密、怀疑盗号、离职交接时用）。

    与用户自助「退出全部设备」等价，但由管理员发起，所以必须留痕：
    这是「让别人立刻失去访问权」的操作，事后要能回答是谁做的、踢掉了几台。
    """
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if not user:
        raise AppException(code=404, message="用户不存在")

    revoked = await revoke_all_sessions(user_id)
    # 版本号递增：手上没过期的 access_token 也一并作废（会话簿只能管住 refresh）
    await bump_token_version(user_id)

    record_admin_action(
        db,
        admin,
        "user_force_logout",
        target_type="user",
        target_id=user.id,
        summary=f"强制下线用户 {user.account}（{len(revoked)} 个会话）",
        detail={"target_account": user.account, "revoked": len(revoked)},
    )
    await db.commit()
    logger.bind(event="admin_force_logout", admin_id=admin.id, user_id=user.id).info(
        f"管理员强制下线用户 {user.id}（{len(revoked)} 个会话）"
    )
    return len(revoked)
