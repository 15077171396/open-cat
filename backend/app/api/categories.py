"""分类路由"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_types import label_for
from app.database import get_db
from app.core.rbac import require_permission
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import success_response, AppException
from app.schemas.category import (
    CategoryCreate,
    CategoryModuleItem,
    CategoryModulesData,
    CategoryReorder,
    CategoryUpdate,
)
from app.schemas.common import ApiResponse
from app.services.category_service import (
    get_cached_categories,
    get_module_overview,
    create_category,
    update_category,
    delete_category,
    reorder_categories,
    MODULE_TABLE_MAP,
)
from app.schemas.admin import ActionAck, CreatedResult, StatsResult

router = APIRouter()


@router.get("", response_model=ApiResponse[CategoryModulesData])
async def list_category_modules():
    """可管理分类的内容类型清单（前端 Tab / 分组**从这里取**，不再硬编码）。

    为什么单独有这么一条：分类页原先在前端写死了 `tutorial/resource/node/plugin/tool`
    五个 Tab，模块下线后前端不会报错，只会留下「点进去 422」的死入口。
    清单派生自 `MODULE_TABLE_MAP`（= 内容类型注册表），与后端校验同源。
    """
    return success_response(
        CategoryModulesData(
            modules=[
                CategoryModuleItem(key=module, label=label_for(module))
                for module in MODULE_TABLE_MAP
            ]
        )
    )


def _assert_known_module(module: str) -> None:
    """模块白名单校验（复用 service 层映射表，新增模块时无需同步两处）"""
    if module not in MODULE_TABLE_MAP:
        raise AppException(code=400, message="无效的模块名")


@router.get("/{module}", response_model=ApiResponse[list])
async def get_categories(module: str, db: AsyncSession = Depends(get_db)):
    """获取指定模块的分类列表(公开接口，Redis 缓存 120s，管理端写操作主动失效)"""
    _assert_known_module(module)
    categories = await get_cached_categories(db, module)
    return success_response(categories)


@router.get("/{module}/overview", response_model=ApiResponse[StatsResult])
async def get_categories_overview(
    module: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
    _: User = Depends(require_permission("category:view")),
):
    """分类管理页专用：分类 + 各分类内容数 + 未登记分类（孤儿）。**实时查询，不走缓存**

    与公开的 `GET /{module}` 分开的理由见 `category_service.get_module_overview`：
    内容数量会随内容增删变化，而分类缓存的失效只由分类写操作触发。
    """
    _assert_known_module(module)
    return success_response(await get_module_overview(db, module))


@router.put("/{module}/reorder", response_model=ApiResponse[list])
async def reorder_categories_endpoint(
    module: str,
    body: CategoryReorder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
    _: User = Depends(require_permission("category:edit")),
):
    """整组重排分类(管理员，需 category:edit 权限)——闸口说明见 create_category_endpoint"""
    _assert_known_module(module)
    return success_response(await reorder_categories(db, module, body.ids), "排序已更新")


@router.post("", response_model=ApiResponse[CreatedResult])
async def create_category_endpoint(
    body: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
    _: User = Depends(require_permission("category:create")),
):
    """创建分类(管理员，需 category:create 权限)。require_admin 与 require_permission 缺一不可：
    后者只查 RBAC 权限码，而 user 角色账号也能被分配权限，单靠它会让业务上非管理员的账号写站内公共数据"""
    category = await create_category(db, body.module, body.name, body.group_id)
    return success_response(
        {
            "id": category.id,
            "module": category.module,
            "name": category.name,
            "group_id": category.group_id,
            "sort_order": category.sort_order,
        },
        "创建成功",
    )


@router.put("/{category_id}", response_model=ApiResponse[ActionAck])
async def update_category_endpoint(
    category_id: int,
    body: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
    _: User = Depends(require_permission("category:edit")),
):
    """更新分类(管理员，需 category:edit 权限)——闸口说明见 create_category_endpoint"""
    category = await update_category(db, category_id, body.name, body.group_id)
    return success_response(
        {
            "id": category.id,
            "module": category.module,
            "name": category.name,
            "group_id": category.group_id,
            "sort_order": category.sort_order,
        },
        "更新成功",
    )


@router.delete("/{category_id}", response_model=ApiResponse[None])
async def delete_category_endpoint(
    category_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
    _: User = Depends(require_permission("category:delete")),
):
    """删除分类(管理员，需 category:delete 权限)——闸口说明见 create_category_endpoint"""
    await delete_category(db, category_id)
    return success_response(message="删除成功")
