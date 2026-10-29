"""列表导出端点：`GET /admin/export/{resource}` 系列。

为什么按资源拆成多条路径，而不是一个 `/export?resource=users`：

- 权限走的是 `core/rbac.py` 的**路径级 fail-closed 规则表**。一条动态路径无法表达
  「导出用户要 user:view、导出教程要 tutorial:view」，只能在端点里手写二次校验，
  而那是这套系统刻意避免的形态（权限只有一个来源）。
- 拆开之后每个端点自然带自己的筛选参数，OpenAPI 里也能看出「用户导出支持哪些筛选」。

导出是**数据外带**，所以每个端点都写审计（谁、什么资源、多少行、什么筛选条件）。
审计在响应开始前落库：流式传输中途断开也要留下「发起过导出」的记录。
"""

from fastapi import Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.common import KEYWORD_MAX_LENGTH
from app.services import export_service


async def _csv_response(
    db: AsyncSession,
    admin: User,
    resource: str,
    filters: dict,
) -> StreamingResponse:
    """统一出口：先算行数并落审计，再返回流式响应。"""
    spec = export_service.get_spec(resource)
    total = await export_service.count_rows(db, spec, filters)
    # 审计在响应开始前落库：流式中途断开也要留下「发起过导出」的记录
    await export_service.record_export(db, admin, spec, filters, total)

    return StreamingResponse(
        export_service.stream_csv(spec, filters),
        media_type="text/csv; charset=utf-8",
        headers={
            # 文件名含时间戳：同一资源多次导出不会互相覆盖
            "Content-Disposition": f'attachment; filename="{export_service.filename(spec)}"',
            # 让浏览器/网关知道这是下载而不是可缓存页面
            "Cache-Control": "no-store",
        },
    )


@router.get("/export/users")
async def export_users(
    role: str = Query(None, max_length=20),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH, description="按账号模糊筛选"),
    account_status: str = Query("active", description="active / deleted / all"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """导出用户列表（CSV）。筛选口径与 `/admin/users` 完全一致。"""
    filters = export_service.pick_filters(
        export_service.get_spec("users"),
        {"role": role, "keyword": keyword, "account_status": account_status},
    )
    return await _csv_response(db, admin, "users", filters)


@router.get("/export/tutorials")
async def export_tutorials(
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None, max_length=50),
    status: str = Query(None, max_length=20),
    is_published: bool = Query(None),
    access_mode: str = Query(None, max_length=20),
    deleted: str = Query("active", description="active / deleted / all"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """导出教程列表（CSV）。"""
    filters = export_service.pick_filters(
        export_service.get_spec("tutorials"),
        {
            "keyword": keyword,
            "category": category,
            "status": status,
            "is_published": is_published,
            "access_mode": access_mode,
            "deleted": deleted,
        },
    )
    return await _csv_response(db, admin, "tutorials", filters)


@router.get("/export/resources")
async def export_resources(
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    resource_type: str = Query(None, max_length=30),
    is_featured: bool = Query(None),
    deleted: str = Query("active", description="active / deleted / all"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """导出资源列表（CSV）。"""
    filters = export_service.pick_filters(
        export_service.get_spec("resources"),
        {
            "keyword": keyword,
            "resource_type": resource_type,
            "is_featured": is_featured,
            "deleted": deleted,
        },
    )
    return await _csv_response(db, admin, "resources", filters)


@router.get("/export/point-transactions")
async def export_point_transactions(
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    type: str = Query(None, max_length=30),
    direction: str = Query(None, max_length=10, description="income / expense"),
    start_at: str = Query(None, max_length=40, description="ISO 时间（含时区偏移）"),
    end_at: str = Query(None, max_length=40, description="ISO 时间（含时区偏移）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """导出积分流水（CSV）。时间参数与列表接口同语义：带偏移的 ISO，内部换算为 naive UTC。"""
    filters = export_service.pick_filters(
        export_service.get_spec("point-transactions"),
        {
            "keyword": keyword,
            "type": type,
            "direction": direction,
            "start_at": start_at,
            "end_at": end_at,
        },
    )
    return await _csv_response(db, admin, "point-transactions", filters)
