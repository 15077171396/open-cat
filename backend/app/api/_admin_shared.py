"""管理后台路由的共享基础：router 对象 + 跨域复用的模型。

所有域必须挂在同一个 router 对象上：app/api/router.py 以 admin.router 挂载（挂载处零改动），
且 FastAPI 按注册顺序匹配路由（batch-delete 必须先于 {id}），单 router 让注册顺序等于各模块 import 顺序。
各域模块从这里 import router 注册路由；prefix 由 router.py include 时统一添加。
"""

from fastapi import APIRouter, Depends

from app.core.rbac import check_route_permission

# 路由级双守卫：require_admin 保证业务角色门槛（users.role='admin'），
# check_route_permission 按 core/rbac.py 规则表做细粒度权限校验
# （super_admin 绕过；未登记规则的新端点 fail-closed 403）
router = APIRouter(dependencies=[Depends(check_route_permission)])
