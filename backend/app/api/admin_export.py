"""列表导出端点：`GET /admin/export/{resource}` 系列（用户 / 教程）。

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


async def _xlsx_response(
    db: AsyncSession,
    admin: User,
    resource: str,
    filters: dict,
) -> StreamingResponse:
    """Excel 出口：审计口径与 CSV 相同，工作簿在内存构建（行数有 MAX_ROWS 上限）。"""
    spec = export_service.get_spec(resource)
    total = await export_service.count_rows(db, spec, filters)
    await export_service.record_export(db, admin, spec, filters, total)

    content = await export_service.build_xlsx(spec, filters)
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{export_service.filename(spec, "xlsx")}"',
            "Cache-Control": "no-store",
        },
    )


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
            "deleted": deleted,
        },
    )
    return await _csv_response(db, admin, "tutorials", filters)


@router.get("/export/audit-logs")
async def export_audit_logs(
    action: str = Query(None, max_length=60),
    target_type: str = Query(None, max_length=40),
    risk: str = Query(None, max_length=20),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    start_at: str = Query(None, max_length=40),
    end_at: str = Query(None, max_length=40),
    format: str = Query("csv", pattern="^(csv|xlsx)$", description="导出格式"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """导出操作审计（CSV / Excel 双格式）。审计是只读证据，导出不改任何数据，但「数据外带」本身写审计。"""
    from app.utils.time import parse_iso_to_naive_utc

    filters = export_service.pick_filters(
        export_service.get_spec("audit-logs"),
        {
            "action": action,
            "target_type": target_type,
            "risk": risk,
            "keyword": keyword,
            "start_at": parse_iso_to_naive_utc(start_at),
            "end_at": parse_iso_to_naive_utc(end_at),
        },
    )
    if format == "xlsx":
        return await _xlsx_response(db, admin, "audit-logs", filters)
    return await _csv_response(db, admin, "audit-logs", filters)


@router.get("/export/login-logs")
async def export_login_logs(
    account: str = Query(None, max_length=KEYWORD_MAX_LENGTH, description="按账号筛选（输入完整账号，脱敏后精确匹配）"),
    status: str = Query(None, max_length=16, description="success / failed / logout"),
    start_at: str = Query(None, max_length=40),
    end_at: str = Query(None, max_length=40),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """导出登录日志（CSV）。筛选口径与 `/admin/login-logs` 列表一致。"""
    from app.utils.time import parse_iso_to_naive_utc

    filters = export_service.pick_filters(
        export_service.get_spec("login-logs"),
        {
            "account": account,
            "status": status,
            "start_at": parse_iso_to_naive_utc(start_at),
            "end_at": parse_iso_to_naive_utc(end_at),
        },
    )
    return await _csv_response(db, admin, "login-logs", filters)


@router.get("/export/ops-metrics")
async def export_ops_metrics(
    format: str = Query("csv", pattern="^(csv|xlsx)$", description="导出格式"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """导出核心指标快照（CSV / Excel 双格式）。运维归档用；每行一帧快照，列 = 各指标键。"""
    filters = export_service.pick_filters(
        export_service.get_spec("ops-metrics"),
        {},
    )
    if format == "xlsx":
        return await _xlsx_response(db, admin, "ops-metrics", filters)
    return await _csv_response(db, admin, "ops-metrics", filters)
