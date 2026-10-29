"""安装向导的业务逻辑（API 层只做 IO，见 `tests/test_rbac.py` 的分层守卫）。

三条不变量：
1. **只能成功一次**：存在任何未删除的 admin 就拒绝（409）。这不是体验问题而是安全问题——
   否则任何人都能给自己开一个超管账号。
2. **口令必须哈希**：bcrypt 只认前 72 字节且不做静默截断（`core/security.py` 会拒绝超长），
   所以长度限制在 8–50。
3. **RBAC 绑定失败要降级**：全新库的迁移顺序不保证角色表已就绪，绑定不上只提示
   「去后台手动分配」，不能让整个初始化失败（否则空库环境彻底进不去）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import hash_password_async
from app.models.user import User

#: 口令长度下限：低于此值的口令在公网一小时内就会被撞库撞开
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 50


class FirstAdminResult(NamedTuple):
    user_id: int
    super_admin_bound: bool


async def count_live_admins(db: AsyncSession) -> int:
    """未软删除的管理员数量（软删的不算，否则注销后再也无法初始化干净环境）。"""
    stmt = select(func.count()).select_from(User).where(
        User.role == "admin", User.deleted_at.is_(None)
    )
    return int((await db.execute(stmt)).scalar() or 0)


async def needs_setup(db: AsyncSession) -> bool:
    return await count_live_admins(db) == 0


async def create_first_admin(
    db: AsyncSession,
    *,
    account: str,
    password: str,
    nickname: str | None = None,
) -> FirstAdminResult:
    """创建首个管理员并提交；已存在管理员时抛 409。"""
    if not await needs_setup(db):
        # 不透露"已有几个管理员"，前端本来就会先查 /status
        raise AppException(code=409, message="系统已完成初始化，请直接登录")

    account = account.strip()
    if not account:
        raise AppException(code=400, message="账号不能为空")
    if password.strip().lower() == account.lower():
        raise AppException(code=400, message="口令不能与账号相同")
    if not (MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN):
        raise AppException(
            code=400, message=f"口令长度需在 {MIN_PASSWORD_LEN}–{MAX_PASSWORD_LEN} 位之间"
        )

    admin = User(
        account=account,
        password_hash=await hash_password_async(password),
        role="admin",
        nickname=(nickname or "").strip() or None,
        free_used_seconds=0,
        # 必须显式写：该列无 server_default，漏写会落 NULL，
        # 前台只能回退 created_at 算免费时长（时区语义不同源）
        free_started_at=datetime.now(timezone.utc),
    )
    db.add(admin)
    await db.flush()

    bound = await _bind_super_admin(db, admin.id)
    await db.commit()
    logger.warning(
        f"安装向导创建了首个管理员: account={account} super_admin={bound}"
        "（该接口自此永久关闭）"
    )
    return FirstAdminResult(user_id=admin.id, super_admin_bound=bound)


async def _bind_super_admin(db: AsyncSession, user_id: int) -> bool:
    """绑定内置 super_admin 角色；角色表未就绪时返回 False 而不是让初始化失败。"""
    try:
        from app.models.rbac import Role, UserRole
    except ImportError:  # 模型缺失属部署异常，不该阻断建号
        logger.warning("RBAC 模型不可用，跳过 super_admin 绑定")
        return False

    try:
        role = (
            await db.execute(select(Role).where(Role.code == "super_admin"))
        ).scalar_one_or_none()
        if role is None:
            return False
        db.add(UserRole(user_id=user_id, role_id=role.id))
        return True
    except SQLAlchemyError as exc:
        # 只捕获数据库异常：绑定失败是降级而非致命（也可能角色表还没迁移）
        logger.error(f"安装向导绑定 super_admin 失败（不影响建号）: {exc}")
        return False
