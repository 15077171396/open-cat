"""岗位目录 CRUD。岗位暂作为组织架构的独立目录；用户绑定后续接入。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.position import Position
from app.models.user import User
from app.services.admin_audit_service import diff_values, record_admin_action


def _row(position: Position) -> dict:
    return {
        "id": position.id,
        "code": position.code,
        "name": position.name,
        "description": position.description,
        "sort_order": position.sort_order,
        "is_enabled": bool(position.is_enabled),
    }


async def _all(db: AsyncSession) -> list[Position]:
    result = await db.execute(select(Position))
    return list(result.scalars().all())


async def _get(db: AsyncSession, position_id: int) -> Position:
    result = await db.execute(select(Position).where(Position.id == position_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise AppException(code=404, message="岗位不存在")
    return row


async def list_positions(db: AsyncSession, keyword: str = "") -> dict:
    needle = keyword.strip().lower()
    rows = [
        _row(row)
        for row in await _all(db)
        if not needle
        or needle in row.code.lower()
        or needle in row.name.lower()
        or needle in (row.description or "").lower()
    ]
    rows.sort(key=lambda item: (item["sort_order"], item["id"]))
    return {"items": rows}


async def create_position(
    db: AsyncSession,
    admin: User,
    *,
    code: str,
    name: str,
    description: str | None,
    sort_order: int,
    is_enabled: bool,
) -> dict:
    code = code.strip()
    name = name.strip()
    if not code or not name:
        raise AppException(code=400, message="岗位编码与名称不能为空")
    existing = await _all(db)
    if any(row.code == code for row in existing):
        raise AppException(code=400, message=f"岗位编码「{code}」已存在")
    if any(row.name == name for row in existing):
        raise AppException(code=400, message=f"岗位名称「{name}」已存在")
    row = Position(
        code=code,
        name=name,
        description=(description or "").strip() or None,
        sort_order=sort_order,
        is_enabled=is_enabled,
    )
    db.add(row)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "position_create",
        target_type="position",
        target_id=row.id,
        summary=f"新建岗位「{name}」（{code}）",
    )
    await db.commit()
    return _row(row)


async def update_position(
    db: AsyncSession,
    admin: User,
    position_id: int,
    changes: dict,
) -> dict:
    row = await _get(db, position_id)
    before = _row(row)
    if "name" in changes and changes["name"] is not None:
        name = str(changes["name"]).strip()
        if not name:
            raise AppException(code=400, message="岗位名称不能为空")
        if any(item.id != row.id and item.name == name for item in await _all(db)):
            raise AppException(code=400, message=f"岗位名称「{name}」已存在")
        row.name = name
    if "description" in changes:
        row.description = (str(changes["description"]).strip() or None) if changes["description"] is not None else None
    if "sort_order" in changes and changes["sort_order"] is not None:
        row.sort_order = int(changes["sort_order"])
    if "is_enabled" in changes and changes["is_enabled"] is not None:
        row.is_enabled = bool(changes["is_enabled"])
    after = _row(row)
    record_admin_action(
        db,
        admin,
        "position_update",
        target_type="position",
        target_id=row.id,
        summary=f"编辑岗位「{row.name}」（{row.code}）",
        detail={"changes": diff_values(before, after, fields=before.keys())},
    )
    await db.commit()
    return after


async def delete_position(db: AsyncSession, admin: User, position_id: int) -> None:
    row = await _get(db, position_id)
    record_admin_action(
        db,
        admin,
        "position_delete",
        target_type="position",
        target_id=row.id,
        summary=f"删除岗位「{row.name}」（{row.code}）",
    )
    await db.delete(row)
    await db.commit()
