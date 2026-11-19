"""管理后台路由的共享基础：router 对象 + 跨域复用的守卫依赖。

所有域必须挂在同一个 router 对象上：app/api/router.py 以 admin.router 挂载（挂载处零改动），
且 FastAPI 按注册顺序匹配路由（batch-delete 必须先于 {id}），单 router 让注册顺序等于各模块 import 顺序。
各域模块从这里 import router 注册路由；prefix 由 router.py include 时统一添加。
"""

from fastapi import APIRouter, Depends, Header

from app.core.permissions import require_admin
from app.core.rbac import check_route_permission

# 路由级双守卫：require_admin 保证业务角色门槛（users.role='admin'），
# check_route_permission 按 core/rbac.py 规则表做细粒度权限校验
# （super_admin 绕过；未登记规则的新端点 fail-closed 403）
router = APIRouter(dependencies=[Depends(check_route_permission)])


async def require_elevation(
    x_elevation_token: str | None = Header(None, alias="X-Elevation-Token"),
    admin=Depends(require_admin),
) -> None:
    """危险操作二次校验依赖（S5，2026-11-20）。

    挂在不可逆端点（吊销令牌 / 删除 Webhook / 删用户等）上：请求必须携带
    `X-Elevation-Token` —— 由 POST /admin/elevation 重新输密码换来的 5 分钟
    短时凭据（见 core/elevation.py）。缺头 / 不匹配 / 过期一律 403，消息里
    给出修复路径。放在本文件而不是 dependencies.py：core.permissions 顶层
    引用 dependencies（get_current_user），把本依赖放那边会形成导入环。
    """
    from app.core.elevation import verify

    if not await verify(admin.id, x_elevation_token):
        from app.core.exceptions import AppException

        raise AppException(
            code=403,
            message="危险操作需先通过密码二次校验：先调 POST /api/v1/admin/elevation 重新输入密码，再携带 X-Elevation-Token 重试",
        )
