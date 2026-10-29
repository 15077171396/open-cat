"""管理后台 - 侧边菜单。

侧边栏结构的唯一出口：前端 `AdminLayout` 拉取本接口渲染菜单，
菜单配置声明在 `app/services/admin_menu.py`（改菜单只改那份文件）。

⚠️ 必须走 `_admin_shared.router`（已挂 require_admin + check_route_permission），
且在 `core/rbac.py` 规则表登记 —— 未登记的新端点 fail-closed 403。
本接口登记为**空权限列表**（仅需 admin 门槛）：菜单结构不含业务数据，
可见性过滤由前端按自身 `permissions` 完成，这样权限被撤销后无需重拉菜单即时生效。
"""

from fastapi import Depends

from app.core.permissions import require_admin
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse
from app.schemas.admin import ActionAck
from app.models.user import User
from app.services import admin_menu
from app.api._admin_shared import router


@router.get("/menus", response_model=ApiResponse[ActionAck])
async def admin_menu_tree(
    admin: User = Depends(require_admin),
):
    """侧边菜单树（分组 + 叶子项 + 权限码 + 图标名）。

    返回**全量结构**，不按用户过滤：过滤逻辑单一地放在前端
    （`AdminLayout` 的 visibleMenuItems），避免两端各有一份权限口径。
    """
    return success_response(admin_menu.menu_tree())
