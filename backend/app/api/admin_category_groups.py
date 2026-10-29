"""分类分组：后台增删改查 + 把分类移入/移出分组。

为什么单独放在 /admin 命名空间
------------------------------
分组只在后台维护，前台不消费（前台拿分类名筛选内容）。放在 `/admin` 下才能走
`core/rbac.py` 的 fail-closed 规则表 —— 未登记的新端点直接 403，不会因为「忘了加
权限判断」而裸奔。权限码复用 `category:*`：分组是分类的附属结构，单独造一套码
只会让角色配置更碎。

删除语义
--------
组下还有分类时**拒绝删除**（不级联）：分类被内容引用着，静默删掉会让内容指向
不存在的分类，前台按该分类筛出来是空的。
"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import AppException, success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck, CreatedResult
from app.schemas.category import (
    CategoryGroupCreate,
    CategoryGroupItem,
    CategoryGroupReorder,
    CategoryGroupUpdate,
    CategoryMoveRequest,
)
from app.schemas.common import ApiResponse
from app.schemas.content import ItemsResult
from app.services import category_service
from app.services.category_service import MODULE_TABLE_MAP


def _assert_known_module(module: str) -> None:
    if module not in MODULE_TABLE_MAP:
        raise AppException(code=400, message="无效的模块名")


@router.get("/category-groups", response_model=ApiResponse[ItemsResult[CategoryGroupItem]])
async def list_category_groups(
    module: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """某内容类型下的分组清单（按 sort_order 升序）。"""
    _assert_known_module(module)
    groups = await category_service.list_groups(db, module)
    return success_response(
        {"items": [CategoryGroupItem(id=g.id, name=g.name, sort_order=g.sort_order) for g in groups]}
    )


@router.post("/category-groups", response_model=ApiResponse[CreatedResult])
async def create_category_group(
    body: CategoryGroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    group = await category_service.create_group(db, body.module, body.name, current_user)
    return success_response({"id": group.id}, "分组已创建")


@router.put("/category-groups/reorder", response_model=ApiResponse[list])
async def reorder_category_groups(
    body: CategoryGroupReorder,
    module: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    _assert_known_module(module)
    return success_response(
        await category_service.reorder_groups(db, module, body.ids, current_user), "排序已更新"
    )


@router.put("/category-groups/{group_id}", response_model=ApiResponse[ActionAck])
async def rename_category_group(
    group_id: int,
    body: CategoryGroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    group = await category_service.rename_group(db, group_id, body.name, current_user)
    return success_response({"id": group.id, "name": group.name}, "分组已重命名")


@router.delete("/category-groups/{group_id}", response_model=ApiResponse[ActionAck])
async def delete_category_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await category_service.delete_group(db, group_id, current_user)
    return success_response(message="分组已删除")


@router.put("/categories/{category_id}/group", response_model=ApiResponse[ActionAck])
async def move_category_to_group(
    category_id: int,
    body: CategoryMoveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """把分类移入分组；`group_id=null` 表示移出分组（回到「未分组」）。"""
    await category_service.move_category(db, category_id, body.group_id, current_user)
    return success_response(message="已移动")
