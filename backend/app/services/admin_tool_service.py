"""管理后台 - 在线工具 CRUD 服务

与其他 admin_*_service 保持相同响应格式。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.tool import Tool
from app.models.user import User
from app.core.exceptions import AppException
from app.services.search import TOOL_SEARCH_COLS, apply_keyword_filter
from app.services.content_cache import invalidate_tools
from app.services.admin_audit_service import audit_changes, record_admin_action

# 与 Tool 模型列定义对齐
_MAX_NAME = 100
_MAX_DESCRIPTION = 500

# 工具使用权限合法值
_ACCESS_MODES = ("free", "auth", "points")


def _normalize_access_fields(data: dict, *, partial: bool) -> None:
    """校验并规范化使用权限字段（原位修改 data）

    - access_mode 非法值直接 400
    - points_cost 负数直接 400；points 模式下缺省按 1 处理
    """
    access_mode = data.get("access_mode")
    if access_mode is not None:
        if access_mode not in _ACCESS_MODES:
            raise AppException(code=400, message="使用权限无效，仅支持 free/auth/points")
    elif not partial:
        data["access_mode"] = "auth"

    points_cost = data.get("points_cost")
    if points_cost is not None:
        if not isinstance(points_cost, int) or points_cost < 0 or points_cost > 100000:
            raise AppException(code=400, message="积分消耗必须为 0-100000 的整数")
    elif not partial and data.get("access_mode") == "points":
        data["points_cost"] = 1


async def list_tools(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str = None,
    category: str = None,
    tool_type: str = None,
) -> dict:
    """工具列表（含搜索 / 分类与类型筛选 / 分页）"""
    conditions = []
    if category and category != "all":
        conditions.append(Tool.category == category)
    if tool_type and tool_type != "all":
        conditions.append(Tool.tool_type == tool_type)

    # 与其它四个域同一个检索入口（第四轮审查 P2-30）：原先这里和 public 侧各手写一份
    # `escape_like + or_(contains)`，加一列检索目标就得记得改两处。
    # ⚠️ 放在所有其它条件之后调用：返回的 total 就是当时条件集的命中数。
    total = await apply_keyword_filter(db, Tool, conditions, TOOL_SEARCH_COLS, keyword, log_search=False)
    # 列表不需要 JSON 大字段，load_only 裁剪
    query = (
        select(Tool)
        .options(
            load_only(
                Tool.id, Tool.name, Tool.description, Tool.category,
                Tool.tool_type, Tool.usage_count, Tool.is_published,
                Tool.access_mode, Tool.points_cost,
            )
        )
        .where(*conditions)
        .order_by(Tool.created_at.desc(), Tool.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tools = (await db.execute(query)).scalars().all()
    items = [
        {
            "id": t.id,
            "name": t.name,
            "description": (t.description or "")[:80],
            "category": t.category,
            "tool_type": t.tool_type,
            "usage_count": t.usage_count,
            "is_published": t.is_published,
            "access_mode": t.access_mode or "auth",
            "points_cost": t.points_cost or 0,
        }
        for t in tools
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_tool(db: AsyncSession, tool_id: int) -> dict:
    """工具详情"""
    tool = (
        await db.execute(select(Tool).where(Tool.id == tool_id))
    ).scalar_one_or_none()
    if not tool:
        raise AppException(code=404, message="工具不存在")
    return {
        "id": tool.id,
        "name": tool.name,
        "description": tool.description,
        "category": tool.category,
        "tool_type": tool.tool_type,
        "ai_config": tool.ai_config,
        "form_schema": tool.form_schema,
        "usage_count": tool.usage_count,
        "is_published": tool.is_published,
        "access_mode": tool.access_mode or "auth",
        "points_cost": tool.points_cost or 0,
    }


def _validate_tool_payload(data: dict, *, partial: bool) -> dict:
    """字段长度校验，超限直接 400，避免入库 DataError 500"""
    name = data.get("name")
    if name is not None and len(name) > _MAX_NAME:
        raise AppException(code=400, message=f"工具名称不能超过 {_MAX_NAME} 字")
    description = data.get("description")
    if description is not None and len(description) > _MAX_DESCRIPTION:
        raise AppException(code=400, message=f"简介不能超过 {_MAX_DESCRIPTION} 字")
    if not partial and not name:
        raise AppException(code=400, message="工具名称不能为空")
    return data


async def create_tool(db: AsyncSession, data: dict, admin: User) -> dict:
    """创建工具

    data 字段：name/description/category/tool_type/ai_config/form_schema/is_published/access_mode/points_cost
    """
    _validate_tool_payload(data, partial=False)
    _normalize_access_fields(data, partial=False)
    tool = Tool(
        name=data["name"],
        description=data.get("description") or "",
        category=data.get("category") or "其他",
        tool_type=data.get("tool_type") or "ai",
        ai_config=data.get("ai_config"),
        form_schema=data.get("form_schema"),
        is_published=data.get("is_published", True),
        access_mode=data.get("access_mode") or "auth",
        points_cost=data.get("points_cost") if data.get("access_mode") == "points" else 0,
    )
    db.add(tool)
    await db.flush()  # 先拿到 generate_id 生成的主键，审计才能归因到具体工具
    record_admin_action(
        db, admin, "content_create",
        target_type="tool", target_id=tool.id,
        summary=f"新建工具《{tool.name}》",
        detail={"tool_type": tool.tool_type, "is_published": tool.is_published},
    )
    # 显式提交：确保响应返回前事务已落库（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    await invalidate_tools()
    return {"id": tool.id}


async def update_tool(
    db: AsyncSession, tool_id: int, updates: dict, admin: User
) -> None:
    """更新工具"""
    tool = (
        await db.execute(select(Tool).where(Tool.id == tool_id))
    ).scalar_one_or_none()
    if not tool:
        raise AppException(code=404, message="工具不存在")
    _validate_tool_payload(updates, partial=True)
    _normalize_access_fields(updates, partial=True)

    for field in ("name", "description", "category", "tool_type",
                  "ai_config", "form_schema", "is_published",
                  "access_mode", "points_cost"):
        if updates.get(field) is not None:
            setattr(tool, field, updates[field])
    # 非积分模式清零积分价格，避免残留误导后台展示
    if tool.access_mode != "points" and tool.points_cost:
        tool.points_cost = 0
    record_admin_action(
        db, admin, "content_update",
        target_type="tool", target_id=tool_id,
        summary=f"编辑工具《{tool.name}》",
        detail={"fields": sorted(updates.keys()), "changes": audit_changes(tool, updates)},
    )
    await db.commit()
    await invalidate_tools()


async def toggle_publish(db: AsyncSession, tool_id: int, admin: User) -> dict:
    """切换上架状态"""
    tool = (
        await db.execute(select(Tool).where(Tool.id == tool_id))
    ).scalar_one_or_none()
    if not tool:
        raise AppException(code=404, message="工具不存在")
    tool.is_published = not tool.is_published
    record_admin_action(
        db, admin, "content_publish",
        target_type="tool", target_id=tool_id,
        summary=f"{'上架' if tool.is_published else '下架'}工具《{tool.name}》",
        detail={"is_published": tool.is_published},
    )
    await db.commit()
    await invalidate_tools()
    return {"id": tool.id, "is_published": tool.is_published}


async def delete_tool(db: AsyncSession, tool_id: int, admin: User) -> None:
    """删除工具（硬删除）"""
    tool = (
        await db.execute(select(Tool).where(Tool.id == tool_id))
    ).scalar_one_or_none()
    if not tool:
        raise AppException(code=404, message="工具不存在")
    name = tool.name
    await db.delete(tool)
    record_admin_action(
        db, admin, "content_delete",
        target_type="tool", target_id=tool_id,
        summary=f"删除工具 #{tool_id}《{name}》",
    )
    await db.commit()
    await invalidate_tools()
