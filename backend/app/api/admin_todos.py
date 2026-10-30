"""管理后台 - 顶栏待办聚合。

只读聚合，不新增业务动作：每一项都跳到原页面处理。
规则表按「仅需 admin 门槛」登记（空权限列表），类别是否出现由服务层按权限裁剪。
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import StatsResult
from app.schemas.common import ApiResponse
from app.services.admin_todo_service import get_admin_todos


@router.get("/todos", response_model=ApiResponse[StatsResult])
async def todos(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """当前管理员的待办汇总：无权限的类别不会出现在结果里。"""
    return success_response(await get_admin_todos(db, admin))
