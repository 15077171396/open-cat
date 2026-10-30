"""菜单管理：读取代码菜单目录 + 保存展示层覆盖（名称/图标/可见/排序）。

权限复用 `setting:view / setting:update`：菜单是全站导航配置，与「系统设置」同一管理层级。
不提供新建/删除：路由与权限码由代码同源定义，凭空加菜单会制造 404/403 死入口。
"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.i18n import request_locale
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck
from app.schemas.admin_menu import MenuOrderUpdate, MenuOverrideUpdate, MenuSettingsData
from app.schemas.common import ApiResponse
from app.services import menu_setting_service


@router.get("/menu-settings", response_model=ApiResponse[MenuSettingsData])
async def get_menu_settings(
    db: AsyncSession = Depends(get_db),
    locale: str = Depends(request_locale),
    admin: User = Depends(require_admin),
):
    """菜单目录 + 当前生效值 + 代码默认值（供管理页编辑）。"""
    return success_response(await menu_setting_service.settings_tree(db, locale))


@router.put("/menu-settings/order", response_model=ApiResponse[ActionAck])
async def update_menu_order(
    payload: MenuOrderUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await menu_setting_service.reorder(
        db, admin, [item.model_dump() for item in payload.items]
    )
    return success_response(result)


@router.put("/menu-settings/item", response_model=ApiResponse[ActionAck])
async def update_menu_override(
    payload: MenuOverrideUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # 菜单 key 是 `/`、`/tutorials` 这类路径，放进 URL path 会被 nginx/Starlette 的
    # 斜杠解码拆段；改成 body/query 传 key，避免编码后的 `%2F` 匹配不到路由。
    changes = {
        field: getattr(payload, field)
        for field in payload.model_fields_set
        if field != "key"
    }
    result = await menu_setting_service.update_override(db, admin, payload.key, changes)
    return success_response(result)


@router.delete("/menu-settings/item", response_model=ApiResponse[ActionAck])
async def reset_menu_override(
    menu_key: str = Query(..., alias="key", min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    await menu_setting_service.reset_one(db, admin, menu_key)
    return success_response({"ok": True})


@router.post("/menu-settings/reset", response_model=ApiResponse[ActionAck])
async def reset_all_menu_overrides(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    count = await menu_setting_service.reset_all(db, admin)
    return success_response({"reset": count})
