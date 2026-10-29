"""权限校验依赖"""

from fastapi import Depends

from app.dependencies import get_current_user
from app.models.user import User
from app.core.exceptions import PermissionDeniedError

# 角色等级
ROLE_HIERARCHY = {
    "guest": 0,
    "user": 1,
    "vip": 2,
    "admin": 3,
}


def require_role(min_role: str):
    """角色权限校验工厂，返回要求 >= min_role 的依赖（用法: Depends(require_role("vip"))）；
    未知角色名直接抛 ValueError（fail-closed），防止拼写错误静默放开权限。"""
    if min_role not in ROLE_HIERARCHY:
        raise ValueError(f"require_role 收到未知角色名: {min_role!r}，可用: {sorted(ROLE_HIERARCHY)}")
    required_level = ROLE_HIERARCHY[min_role]

    async def _checker(current_user: User = Depends(get_current_user)) -> User:
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        if user_level < required_level:
            raise PermissionDeniedError(f"需要 {min_role} 及以上权限")
        return current_user

    return _checker


# 常用预设：VIP 及以上权限校验。用法: Depends(require_vip)
require_vip = require_role("vip")


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """管理员权限校验依赖（保留独立函数维持向后兼容，已有路由直接引用）"""
    user_level = ROLE_HIERARCHY.get(current_user.role, 0)
    required_level = ROLE_HIERARCHY.get("admin", 0)
    if user_level < required_level:
        raise PermissionDeniedError("需要 admin 及以上权限")
    return current_user
