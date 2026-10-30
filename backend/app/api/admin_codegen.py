"""代码生成器：只读表结构，预览 / 下载后端 CRUD 骨架。

权限复用系统设置：查看与预览要求 `setting:view`，下载属于开发数据外带，要求 `setting:update`。
"""

from fastapi import Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.codegen import (
    CodegenDownloadRequest,
    CodegenPreviewData,
    CodegenTablesData,
)
from app.schemas.common import ApiResponse
from app.services import codegen_service


@router.get("/codegen/tables", response_model=ApiResponse[CodegenTablesData])
async def list_codegen_tables(admin: User = Depends(require_admin)):
    return success_response(await codegen_service.get_tables())


@router.get("/codegen/preview", response_model=ApiResponse[CodegenPreviewData])
async def preview_codegen(
    table: str = Query(..., max_length=64),
    module: str = Query(..., min_length=3, max_length=40),
    class_name: str = Query(..., min_length=2, max_length=60),
    title: str = Query("", max_length=80),
    route_prefix: str = Query("", max_length=80),
    fields: str = Query("", max_length=2000),
    admin: User = Depends(require_admin),
):
    selected = [item.strip() for item in fields.split(",") if item.strip()]
    data = await codegen_service.preview(
        table,
        module=module,
        class_name=class_name,
        title=title,
        route_prefix=route_prefix,
        fields=selected,
    )
    return success_response(data)


@router.post("/codegen/download")
async def download_codegen(
    payload: CodegenDownloadRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    filename, content = await codegen_service.download(
        db,
        admin,
        table=payload.table,
        module=payload.module,
        class_name=payload.class_name,
        title=payload.title,
        route_prefix=payload.route_prefix,
        fields=payload.fields,
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
