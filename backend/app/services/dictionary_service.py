"""数据字典：类型与字典项的 CRUD。

- 权限复用 `setting:view / setting:update`（系统配置层级，不另造权限码）；
- 类型 code 创建后不可改（业务代码按 code 引用，改了等于静默断链）；
- 同一类型下 value 唯一、默认项最多一个；
- 删除类型前必须清空字典项（不级联，避免误删）；
- 所有写操作写审计。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.dictionary import DictItem, DictType
from app.models.user import User
from app.services.admin_audit_service import record_admin_action, diff_values

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")


def _row_type(dict_type: DictType, item_count: int) -> dict:
    return {
        "id": dict_type.id,
        "code": dict_type.code,
        "name": dict_type.name,
        "description": dict_type.description,
        "is_enabled": bool(dict_type.is_enabled),
        "item_count": item_count,
    }


def _row_item(item: DictItem) -> dict:
    return {
        "id": item.id,
        "type_id": item.type_id,
        "label": item.label,
        "value": item.value,
        "sort_order": item.sort_order,
        "is_enabled": bool(item.is_enabled),
        "is_default": bool(item.is_default),
        "remark": item.remark,
    }


async def _all_types(db: AsyncSession) -> list[DictType]:
    result = await db.execute(select(DictType))
    return list(result.scalars().all())


async def _all_items(db: AsyncSession, type_id: int | None = None) -> list[DictItem]:
    stmt = select(DictItem)
    if type_id is not None:
        stmt = stmt.where(DictItem.type_id == type_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_type(db: AsyncSession, type_id: int) -> DictType:
    result = await db.execute(select(DictType).where(DictType.id == type_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise AppException(code=404, message="字典类型不存在")
    return row


async def _get_item(db: AsyncSession, type_id: int, item_id: int) -> DictItem:
    result = await db.execute(
        select(DictItem).where(DictItem.id == item_id, DictItem.type_id == type_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise AppException(code=404, message="字典项不存在")
    return row


async def list_types(db: AsyncSession, keyword: str = "") -> dict:
    needle = keyword.strip().lower()
    types = await _all_types(db)
    items = await _all_items(db)
    counts: dict[int, int] = {}
    for item in items:
        counts[item.type_id] = counts.get(item.type_id, 0) + 1
    rows = [
        _row_type(row, counts.get(row.id, 0))
        for row in types
        if not needle
        or needle in row.code.lower()
        or needle in row.name.lower()
        or needle in (row.description or "").lower()
    ]
    rows.sort(key=lambda row: (row["item_count"] == 0, row["code"]))
    return {"items": rows}


async def create_type(
    db: AsyncSession,
    admin: User,
    *,
    code: str,
    name: str,
    description: str | None,
    is_enabled: bool,
) -> dict:
    code = code.strip()
    name = name.strip()
    if not _CODE_RE.match(code):
        raise AppException(code=400, message="类型编码需以小写字母开头，仅含小写字母/数字/下划线（2~80 位）")
    if not name:
        raise AppException(code=400, message="字典名称不能为空")
    existing = [row for row in await _all_types(db) if row.code == code]
    if existing:
        raise AppException(code=400, message=f"类型编码「{code}」已存在")
    row = DictType(
        code=code,
        name=name,
        description=(description or "").strip() or None,
        is_enabled=is_enabled,
    )
    db.add(row)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "dict_type_create",
        target_type="dict_type",
        target_id=row.id,
        summary=f"新建字典类型「{name}」（{code}）",
    )
    await db.commit()
    return _row_type(row, 0)


async def update_type(
    db: AsyncSession,
    admin: User,
    type_id: int,
    changes: dict,
) -> dict:
    row = await _get_type(db, type_id)
    before = {"name": row.name, "description": row.description, "is_enabled": row.is_enabled}
    if "name" in changes and changes["name"] is not None:
        name = str(changes["name"]).strip()
        if not name:
            raise AppException(code=400, message="字典名称不能为空")
        row.name = name
    if "description" in changes:
        row.description = (str(changes["description"]).strip() or None) if changes["description"] is not None else None
    if "is_enabled" in changes and changes["is_enabled"] is not None:
        row.is_enabled = bool(changes["is_enabled"])
    after = {"name": row.name, "description": row.description, "is_enabled": row.is_enabled}
    record_admin_action(
        db,
        admin,
        "dict_type_update",
        target_type="dict_type",
        target_id=row.id,
        summary=f"编辑字典类型「{row.name}」（{row.code}）",
        detail={"changes": diff_values(before, after, fields=before.keys())},
    )
    await db.commit()
    return _row_type(row, len(await _all_items(db, row.id)))


async def delete_type(db: AsyncSession, admin: User, type_id: int) -> None:
    row = await _get_type(db, type_id)
    items = await _all_items(db, type_id)
    if items:
        raise AppException(code=400, message=f"该字典下还有 {len(items)} 个字典项，请先删除字典项")
    record_admin_action(
        db,
        admin,
        "dict_type_delete",
        target_type="dict_type",
        target_id=row.id,
        summary=f"删除字典类型「{row.name}」（{row.code}）",
    )
    await db.delete(row)
    await db.commit()


async def list_items(db: AsyncSession, type_id: int, keyword: str = "") -> list[dict]:
    await _get_type(db, type_id)
    needle = keyword.strip().lower()
    items = await _all_items(db, type_id)
    rows = [
        _row_item(item)
        for item in items
        if not needle
        or needle in item.label.lower()
        or needle in item.value.lower()
        or needle in (item.remark or "").lower()
    ]
    rows.sort(key=lambda row: (row["sort_order"], row["id"]))
    return rows


async def create_item(
    db: AsyncSession,
    admin: User,
    type_id: int,
    *,
    label: str,
    value: str,
    sort_order: int,
    is_enabled: bool,
    is_default: bool,
    remark: str | None,
) -> dict:
    dict_type = await _get_type(db, type_id)
    label = label.strip()
    value = value.strip()
    if not label or not value:
        raise AppException(code=400, message="标签与值都不能为空")
    items = await _all_items(db, type_id)
    if any(item.value == value for item in items):
        raise AppException(code=400, message=f"值「{value}」在该字典下已存在")
    if is_default:
        for item in items:
            item.is_default = False
    row = DictItem(
        type_id=type_id,
        label=label,
        value=value,
        sort_order=sort_order,
        is_enabled=is_enabled,
        is_default=is_default,
        remark=(remark or "").strip() or None,
    )
    db.add(row)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "dict_item_create",
        target_type="dict_item",
        target_id=row.id,
        summary=f"新建字典项「{label}」→「{value}」（{dict_type.code}）",
    )
    await db.commit()
    return _row_item(row)


async def update_item(
    db: AsyncSession,
    admin: User,
    type_id: int,
    item_id: int,
    changes: dict,
) -> dict:
    dict_type = await _get_type(db, type_id)
    row = await _get_item(db, type_id, item_id)
    items = await _all_items(db, type_id)
    before = _row_item(row)
    if "label" in changes and changes["label"] is not None:
        label = str(changes["label"]).strip()
        if not label:
            raise AppException(code=400, message="标签不能为空")
        row.label = label
    if "value" in changes and changes["value"] is not None:
        value = str(changes["value"]).strip()
        if not value:
            raise AppException(code=400, message="值不能为空")
        if any(item.id != row.id and item.value == value for item in items):
            raise AppException(code=400, message=f"值「{value}」在该字典下已存在")
        row.value = value
    if "sort_order" in changes and changes["sort_order"] is not None:
        row.sort_order = int(changes["sort_order"])
    if "is_enabled" in changes and changes["is_enabled"] is not None:
        row.is_enabled = bool(changes["is_enabled"])
    if "remark" in changes:
        row.remark = (str(changes["remark"]).strip() or None) if changes["remark"] is not None else None
    if changes.get("is_default") is True:
        for item in items:
            if item.id != row.id:
                item.is_default = False
        row.is_default = True
    elif changes.get("is_default") is False:
        row.is_default = False
    after = _row_item(row)
    record_admin_action(
        db,
        admin,
        "dict_item_update",
        target_type="dict_item",
        target_id=row.id,
        summary=f"编辑字典项「{row.label}」→「{row.value}」（{dict_type.code}）",
        detail={"changes": diff_values(before, after, fields=before.keys())},
    )
    await db.commit()
    return after


async def delete_item(db: AsyncSession, admin: User, type_id: int, item_id: int) -> None:
    dict_type = await _get_type(db, type_id)
    row = await _get_item(db, type_id, item_id)
    record_admin_action(
        db,
        admin,
        "dict_item_delete",
        target_type="dict_item",
        target_id=row.id,
        summary=f"删除字典项「{row.label}」→「{row.value}」（{dict_type.code}）",
    )
    await db.delete(row)
    await db.commit()
